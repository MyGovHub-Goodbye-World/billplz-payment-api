import json
import os
import requests
import hmac
import hashlib
import datetime
import logging
import uuid
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Secrets will now come directly from environment variables
CALLBACK_URL = os.environ.get("CALLBACK_URL")
REDIRECT_URL = os.environ.get("REDIRECT_URL")

# --- Logging setup ---
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logger = logging.getLogger('payment_handler')
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

def log_struct(level, msg, **kwargs):
    """Emit structured JSON logs which are CloudWatch-friendly."""
    entry = {
        'timestamp': datetime.datetime.utcnow().isoformat() + 'Z',
        'level': level,
        'message': msg,
    }
    entry.update(kwargs)
    # Use the appropriate logger method so handlers and filters work
    if level == 'INFO':
        logger.info(json.dumps(entry))
    elif level == 'ERROR':
        logger.error(json.dumps(entry))
    elif level == 'WARNING':
        logger.warning(json.dumps(entry))
    elif level == 'DEBUG':
        logger.debug(json.dumps(entry))
    else:
        logger.info(json.dumps(entry))

# Billplz API endpoint
BILLPLZ_API_URL = "https://www.billplz-sandbox.com/api/v3/bills"

# MongoDB MCP endpoint
MONGODB_MCP_URL = os.environ.get("MONGODB_MCP_URL")
DB_NAME = os.environ.get("DB_NAME")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME")

# Billplz Configuration
JPJ_COLLECTION_ID = os.environ.get("JPJ_COLLECTION_ID")
TNB_COLLECTION_ID = os.environ.get("TNB_COLLECTION_ID")
JPJ_BILLPLZ_X_SIGNATURE_KEY = os.environ.get("JPJ_BILLPLZ_X_SIGNATURE_KEY")
TNB_BILLPLZ_X_SIGNATURE_KEY = os.environ.get("TNB_BILLPLZ_X_SIGNATURE_KEY")

# --- Database Connection ---
# We initialize the client outside the handlers to reuse the connection across invocations
try:
    client = MongoClient(MONGODB_MCP_URL)
    db = client[DB_NAME]
    collection = db[COLLECTION_NAME]
    # The ismaster command is cheap and does not require auth.
    client.admin.command('ismaster')
    log_struct('INFO', 'MongoDB connection successful')
except ConnectionFailure as e:
    log_struct('ERROR', 'MongoDB connection failed', error=str(e))
    client = None  # Set client to None if connection fails

def create_bill(event, context):
    """
    Creates a transaction in MongoDB, creates a Billplz bill, updates the transaction,
    and returns the payment URL.
    """
    request_id = str(uuid.uuid4())
    log_struct('INFO', 'create_bill invoked', requestId=request_id, eventKeys=list(event.keys()))

    if not client:
        log_struct('ERROR', 'Database connection unavailable', requestId=request_id)
        return {"statusCode": 500, "body": json.dumps({"error": "Database connection failed. Please check logs.", "requestId": request_id})}

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
            log_struct('WARNING', 'Missing required parameters', requestId=request_id, payloadKeys=list(body.keys()))
            return {"statusCode": 400, "body": json.dumps({"error": "Missing required parameters.", "requestId": request_id})}

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
        log_struct('INFO', 'Transaction created', requestId=request_id, transactionId=transaction_id, status='pending')

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

        log_struct('DEBUG', 'Calling Billplz API', requestId=request_id, url=BILLPLZ_API_URL, collectionId=collection_id, amount=amount_in_cents)
        bill_response = requests.post(
            BILLPLZ_API_URL,
            data=billplz_payload,
            auth=(api_key, '')
        )
        bill_response.raise_for_status()
        bill_data = bill_response.json()
        log_struct('INFO', 'Billplz bill created', requestId=request_id, billId=bill_data.get('id'))

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
        log_struct('INFO', 'Transaction updated with Billplz info', requestId=request_id, transactionId=transaction_id, billId=bill_data.get('id'))

        # Detect if redirect URL uses a custom scheme (which may not be handled by clients)
        redirect_url_built = f"{redirect_url}?transactionId={transaction_id}&billplz[id]={bill_data.get('id')}&billplz[paid]=true"
        if '://' in redirect_url and not redirect_url.startswith('http://') and not redirect_url.startswith('https://'):
            log_struct('WARNING', 'Redirect URL uses custom scheme and may not be handled', requestId=request_id, redirect=redirect_url)

        return {"statusCode": 200, "body": json.dumps({"url": bill_data.get("url"), "requestId": request_id})}

    except Exception as e:
        log_struct('ERROR', 'Error in create_bill', requestId=request_id, error=str(e))
        return {"statusCode": 500, "body": json.dumps({"error": str(e), "requestId": request_id})}


def handle_webhook(event, context):
    """Handles incoming webhooks from Billplz and updates the database."""
    request_id = str(uuid.uuid4())
    log_struct('INFO', 'handle_webhook invoked', requestId=request_id, eventKeys=list(event.keys()))

    if not client:
        log_struct('ERROR', 'Database connection unavailable', requestId=request_id)
        return {"statusCode": 500, "body": json.dumps({"error": "Database connection failed. Please check logs.", "requestId": request_id})}

    try:
        body = json.loads(event.get('body', '{}'))
        headers = event.get('headers', {})
        billplz_signature = headers.get('billplz-signature') or headers.get('X-Signature')

        if not verify_signature(body, billplz_signature):
            log_struct('WARNING', 'Invalid webhook signature', requestId=request_id, signature=billplz_signature)
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
                    "billplz.webhookPayload": body,  # For auditing
                    "updatedAt": datetime.datetime.utcnow().isoformat() + "Z"
                }
            }
        )

        if update_result.modified_count == 0:
            log_struct('WARNING', 'No transaction found for bill_id', requestId=request_id, billId=bill_id)
        else:
            log_struct('INFO', 'Successfully processed webhook', requestId=request_id, billId=bill_id, status=paid_status)

        return {"statusCode": 200, "body": "Webhook processed."}

    except Exception as e:
        log_struct('ERROR', 'Error in handle_webhook', requestId=request_id, error=str(e))
        return {"statusCode": 500, "body": json.dumps({"error": str(e), "requestId": request_id})}


def verify_signature(data, signature):
    """Verifies the incoming Billplz webhook X-Signature."""
    if not signature:
        return False
        
    keys = ['amount', 'collection_id', 'due_at', 'email', 'id', 'mobile', 'name', 
            'paid_amount', 'paid_at', 'paid', 'state', 'url']
    
    to_sign_string = "|".join([f"{k}{data.get(k, '')}" for k in keys])

    service_signature = JPJ_BILLPLZ_X_SIGNATURE_KEY if data.get('collection_id') == os.environ.get('JPJ_COLLECTION_ID') else TNB_BILLPLZ_X_SIGNATURE_KEY
    
    hashed = hmac.new(
        service_signature.encode('utf-8'),
        to_sign_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(hashed, signature)