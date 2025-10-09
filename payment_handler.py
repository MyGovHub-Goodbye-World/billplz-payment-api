import json
import os
import requests
import hmac
import hashlib
import datetime
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Secrets will now come directly from environment variables
CALLBACK_URL = os.environ.get("CALLBACK_URL")
REDIRECT_URL = os.environ.get("REDIRECT_URL")

# Billplz API endpoint
BILLPLZ_API_URL = "https://www.billplz-sandbox.com/api/v3/bills"

# Temporary Testing API
FRONTEND_BILLPLZ_COLLECTION_ID_TESTING = os.environ.get("BILLPLZ_COLLECTION_ID_TESTING")
FRONTEND_BILLPLZ_API_KEY_TESTING = os.environ.get("BILLPLZ_API_KEY_TESTING")
BILLPLZ_X_SIGNATURE_KEY = os.environ.get("BILLPLZ_X_SIGNATURE_KEY")

# MongoDB MCP endpoint
MONGODB_MCP_URL = os.environ.get("MONGODB_MCP_URL")
DB_NAME = os.environ.get("DB_NAME")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME")

# --- Database Connection ---
# We initialize the client outside the handlers to reuse the connection across invocations
try:
    client = MongoClient(MONGODB_MCP_URL)
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]
    # The ismaster command is cheap and does not require auth.
    client.admin.command('ismaster')
    print("MongoDB connection successful.")
except ConnectionFailure as e:
    print(f"MongoDB connection failed: {e}")
    client = None # Set client to None if connection fails

def create_bill(event, context):
    """
    Creates a transaction in MongoDB, creates a Billplz bill, updates the transaction,
    and returns the payment URL.
    """
    if not client:
        return {"statusCode": 500, "body": json.dumps({"error": "Database connection failed. Please check logs."})}

    try:
        body = json.loads(event.get('body', '{}'))

        # --- Configurations passed from the API call ---
        api_key = body.get('api_key')
        collection_id = body.get('collection_id')
        callback_url = CALLBACK_URL
        redirect_url = REDIRECT_URL

        # --- Transaction Data ---
        user_id = body.get('user_id')
        service_type = body.get('service_type')
        description = body.get('description')
        amount = float(body.get('amount'))  # In MYR
        amount_in_cents = int(amount * 100)  # Convert to cents
        email = body.get('email')
        name = body.get('name')
        metadata = body.get('metadata', {})

        if not all([api_key, collection_id, callback_url, redirect_url, user_id, amount]):
            return {"statusCode": 400, "body": json.dumps({"error": "Missing required parameters."})}

        # 1. --- Create the initial transaction document ---
        transaction_id = f"txn_{datetime.datetime.utcnow().timestamp()}"
        
        transaction_document = {
            "_id": transaction_id,
            "userId": user_id,
            "serviceType": service_type,
            "description": description,
            "amount": amount,
            "currency": "MYR",
            "status": "pending",
            "createdAt": datetime.datetime.utcnow().isoformat() + "Z",
            "updatedAt": datetime.datetime.utcnow().isoformat() + "Z",
            "metadata": metadata
        }
        
        collection.insert_one(transaction_document)
        print(f"Transaction {transaction_id} created with status 'pending'.")

        # 2. --- Create the Billplz Bill ---
        billplz_payload = {
            'collection_id': collection_id,
            'email': email,
            'name': name,
            'amount': amount_in_cents,
            'description': description,
            'callback_url': callback_url,
            'redirect_url': f"{redirect_url}?transactionId={transaction_id}",
        }

        bill_response = requests.post(
            BILLPLZ_API_URL,
            data=billplz_payload,
            auth=(api_key, '')
        )
        bill_response.raise_for_status()
        bill_data = bill_response.json()
        print(f"Billplz bill created: {bill_data.get('id')}")

        # 3. --- Update the transaction with Billplz details ---
        collection.update_one(
            {"_id": transaction_id},
            {
                "$set": {
                    "billplz": {
                        "billId": bill_data.get('id'),
                        "url": bill_data.get('url')
                    },
                    "updatedAt": datetime.datetime.utcnow().isoformat() + "Z"
                }
            }
        )
        print(f"Transaction {transaction_id} updated with Billplz info.")

        return {"statusCode": 200, "body": json.dumps({"url": bill_data.get("url")})}

    except Exception as e:
        print(f"Error in create_bill: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def handle_webhook(event, context):
    """Handles incoming webhooks from Billplz and updates the database."""
    if not client:
        return {"statusCode": 500, "body": json.dumps({"error": "Database connection failed. Please check logs."})}
    
    try:
        body = json.loads(event.get('body', '{}'))
        headers = event.get('headers', {})
        billplz_signature = headers.get('billplz-signature') or headers.get('X-Signature')

        if not verify_signature(body, billplz_signature):
            print("Invalid webhook signature.")
            return {"statusCode": 400, "body": "Invalid signature"}

        # --- Extract data and update the database ---
        bill_id = body.get('id')
        paid_status = "paid" if body.get('paid') == 'true' else "failed"

        update_result = collection.update_one(
            {"billplz.billId": bill_id},
            {
                "$set": {
                    "status": paid_status,
                    "billplz.paidAt": body.get('paid_at'),
                    "billplz.transactionId": body.get('transaction_id', ''),
                    "billplz.webhookPayload": body, # For auditing
                    "updatedAt": datetime.datetime.utcnow().isoformat() + "Z"
                }
            }
        )

        if update_result.modified_count == 0:
            print(f"Warning: No transaction found for bill_id: {bill_id}. Webhook processed but no update made.")
        else:
            print(f"Successfully processed webhook for bill_id: {bill_id}. Status set to: {paid_status}")

        return {"statusCode": 200, "body": "Webhook processed."}

    except Exception as e:
        print(f"Error in handle_webhook: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def verify_signature(data, signature):
    """Verifies the incoming Billplz webhook X-Signature."""
    if not signature:
        return False
        
    keys = ['amount', 'collection_id', 'due_at', 'email', 'id', 'mobile', 'name', 
            'paid_amount', 'paid_at', 'paid', 'state', 'url']
    
    to_sign_string = "|".join([f"{k}{data.get(k, '')}" for k in keys])
    
    hashed = hmac.new(
        BILLPLZ_X_SIGNATURE_KEY.encode('utf-8'),
        to_sign_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(hashed, signature)