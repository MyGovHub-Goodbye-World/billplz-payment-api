import json
import os
import requests
import hmac

# Secrets will now come directly from environment variables
CALLBACK_URL = os.environ.get("CALLBACK_URL")
REDIRECT_URL = os.environ.get("REDIRECT_URL")

# API Keys
# BILLPLZ_TNB_API_KEY = os.environ.get("BILLPLZ_TNB_API_KEY")
# BILLPLZ_JPJ_API_KEY = os.environ.get("BILLPLZ_JPJ_API_KEY")

# Hard coded URLs
BILLPLZ_API_URL = "https://www.billplz-sandbox.com/api/v3/bills"

# Testing API
FRONTEND_BILLPLZ_COLLECTION_ID_TESTING = os.environ.get("BILLPLZ_COLLECTION_ID_TESTING")
FRONTEND_BILLPLZ_API_KEY_TESTING = os.environ.get("BILLPLZ_API_KEY_TESTING")


def create_bill(event, context):
    """Creates a Billplz bill and returns the payment URL."""
    try:
        body = json.loads(event.get('body', '{}'))
        amount = body.get('amount')
        description = body.get('description')
        email = body.get('email')
        name = body.get('name')
        collection_id = body.get('collection_id')
        api_key = body.get('api_key')

        if not all([amount, description, email, name, collection_id, api_key, CALLBACK_URL, REDIRECT_URL]):
            return {"statusCode": 400, "body": json.dumps({"error": "Missing required parameters or environment variables."})}

        payload = {
            'collection_id': collection_id,
            'email': email,
            'name': name,
            'amount': int(amount), # Amount in cents
            'description': description,
            'callback_url': CALLBACK_URL,
            'redirect_url': REDIRECT_URL,
        }

        response = requests.post(
            BILLPLZ_API_URL,
            data=payload,
            auth=(api_key, '')
        )
        response.raise_for_status()
        bill_data = response.json()
        print(f"Bill created: {bill_data}")

        return {"statusCode": 200, "body": json.dumps({"url": bill_data.get("url")})}
    except Exception as e:
        print(f"Error creating bill: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

# Add your X_SIGNATURE_KEY to your environment variables
BILLPLZ_X_SIGNATURE_KEY = os.environ.get("BILLPLZ_X_SIGNATURE_KEY")

def handle_webhook(event, context):
    """Handles incoming webhooks from Billplz."""
    try:
        body = json.loads(event.get('body', '{}'))
        headers = event.get('headers', {})
        billplz_signature = headers.get('X-Signature')

        # 1. --- SECURITY: Verify the signature ---
        if not verify_signature(body, billplz_signature):
            print("Invalid signature")
            return {"statusCode": 400, "body": "Invalid signature"}

        # 2. --- PROCESS THE DATA ---
        bill_id = body.get('id')
        paid = body.get('paid')  # This will be 'true' or 'false' (as a string)
        state = body.get('state')
        amount = body.get('amount')
        
        # Determine the new status
        if paid == 'true':
            new_status = "Successful"
        else:
            new_status = "Failed"

        # 3. --- UPDATE YOUR DATABASE ---
        # This is where you call your aws-mongodb-mcp service
        # We are assuming you have an endpoint for this
        mongodb_mcp_url = "https://your-api-id.execute-api.us-east-1.amazonaws.com/dev/mongodb-mcp" # Replace with your actual URL
        
        instruction = f"Update transaction with bill reference {bill_id} to status {new_status}"
        
        payload = {
            "instruction": instruction
        }
        
        response = requests.post(mongodb_mcp_url, json=payload)
        response.raise_for_status()

        print(f"Successfully updated transaction for bill_id: {bill_id} to status: {new_status}")

        return {"statusCode": 200, "body": "Webhook received and processed"}

    except Exception as e:
        print(f"Error handling webhook: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def verify_signature(data, signature):
    """Verifies the Billplz X-Signature."""
    if not BILLPLZ_X_SIGNATURE_KEY or not signature:
        return False
        
    # The signature is created by signing the entire body of the webhook
    # The format is `bill_id|bill_paid_status|transaction_id|transaction_status`
    # However, to be safe, we can construct the signature string from the data received.
    
    to_sign = f"amount{data.get('amount')}|collection_id{data.get('collection_id')}|due_at{data.get('due_at')}|email{data.get('email')}|id{data.get('id')}|mobile{data.get('mobile')}|name{data.get('name')}|paid_amount{data.get('paid_amount')}|paid_at{data.get('paid_at')}|paid{data.get('paid')}|state{data.get('state')}|url{data.get('url')}"
    
    # Create the signature using HMAC-SHA256
    hashed = hmac.new(BILLPLZ_X_SIGNATURE_KEY.encode('utf-8'), to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    return hmac.compare_digest(hashed, signature)