import json
import os
import requests
import hmac
import hashlib
from datetime import datetime, timezone
import logging
import uuid
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from decimal import Decimal, ROUND_HALF_UP

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
        'timestamp': datetime.now(timezone.utc).isoformat(),
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
    log_struct('INFO', 'create_bill invoked', eventKeys=list(event.keys()))

    if not client:
        log_struct('ERROR', 'Database connection unavailable')
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
        amount = Decimal(str(body.get('amount'))) # In MYR
        amount_in_cents = int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP)) # Convert to cents
        email = body.get('email')
        name = body.get('name')
        metadata = body.get('metadata', {})

        if not all([api_key, collection_id, callback_url, redirect_url, user_id, amount]):
            log_struct('WARNING', 'Missing required parameters', payloadKeys=list(body.keys()))
            return {"statusCode": 400, "body": json.dumps({"error": "Missing required parameters."})}
        
        timestamp = datetime.now(timezone.utc).isoformat()

        # 1. --- Create the initial transaction document ---
        transaction_id = f"txn_{timestamp}"
        
        transaction_document = {
            "_id": transaction_id,
            "userId": user_id,
            "serviceType": service_type,
            "description": description,
            "amount": float(amount),
            "currency": "MYR",
            "status": "pending",
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "metadata": metadata
        }
        
        collection.insert_one(transaction_document)
        log_struct('INFO', 'Transaction created', transactionId=transaction_id, status='pending')

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

        log_struct('DEBUG', 'Calling Billplz API', url=BILLPLZ_API_URL, collectionId=collection_id, amount=amount_in_cents)
        bill_response = requests.post(
            BILLPLZ_API_URL,
            data=billplz_payload,
            auth=(api_key, '')
        )
        bill_response.raise_for_status()
        bill_data = bill_response.json()
        log_struct('INFO', 'Billplz bill created', billId=bill_data.get('id'))

        # 3. --- Update the transaction with Billplz details ---
        collection.update_one(
            {"_id": transaction_id},
            {
                "$set": {
                    "billplz": {
                        "billId": bill_data.get('id'),
                        "url": bill_data.get('url')
                    },
                    "updatedAt": timestamp
                }
            }
        )
        log_struct('INFO', 'Transaction updated with Billplz info', transactionId=transaction_id, billId=bill_data.get('id'))

        # Detect if redirect URL uses a custom scheme (which may not be handled by clients)
        redirect_url_built = f"{redirect_url}?transactionId={transaction_id}&billplz[id]={bill_data.get('id')}&billplz[paid]=true"
        if '://' in redirect_url and not redirect_url.startswith('http://') and not redirect_url.startswith('https://'):
            log_struct('WARNING', 'Redirect URL uses custom scheme and may not be handled', redirect=redirect_url)

        return {"statusCode": 200, "body": json.dumps({"url": bill_data.get("url")})}

    except Exception as e:
        log_struct('ERROR', 'Error in create_bill', error=str(e))
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def handle_webhook(event, context):
    """Handles incoming webhooks from Billplz and updates the database."""
    log_struct('INFO', 'handle_webhook invoked')

    if not client:
        log_struct('ERROR', 'Database connection unavailable')
        return {"statusCode": 500, "body": "Database connection failed"}

    try:
        # Parse form-encoded webhook data
        from urllib.parse import parse_qs
        
        raw_body = event.get('body', '')
        headers = event.get('headers', {})
        
        # Early debug logging
        log_struct('INFO', 'Webhook received', bodyLength=len(raw_body), headerCount=len(headers))
        
        if not raw_body:
            log_struct('ERROR', 'Empty webhook body')
            return {"statusCode": 400, "body": "Empty body"}
        
        # Parse form data
        parsed_data = parse_qs(raw_body)
        webhook_data = {k: v[0] if v else '' for k, v in parsed_data.items()}
        
        x_signature = headers.get('x-signature') or headers.get('X-Signature')
        
        # Debug logging
        log_struct('INFO', 'Webhook parsed', data=webhook_data, signature=x_signature)
        
        # Temporarily skip signature verification for debugging
        # verify_signature(webhook_data, x_signature)
        
        # Extract bill data
        bill_id = webhook_data.get('id')
        is_paid = webhook_data.get('paid') == 'true'
        
        if not bill_id:
            log_struct('ERROR', 'Missing bill ID in webhook')
            return {"statusCode": 400, "body": "Missing bill ID"}
        
        # Update transaction status
        status = "paid" if is_paid else "failed"
        update_data = {
            "status": status,
            "billplz.paidAt": webhook_data.get('paid_at'),
            "billplz.paidAmount": webhook_data.get('paid_amount'),
            "updatedAt": datetime.now(timezone.utc).isoformat()
        }
        
        result = collection.update_one(
            {"billplz.billId": bill_id},
            {"$set": update_data}
        )
        
        if result.modified_count == 0:
            log_struct('WARNING', 'No transaction found', billId=bill_id)
        else:
            log_struct('INFO', 'Webhook processed', billId=bill_id, status=status)
        
        return {"statusCode": 200, "body": "OK"}
        
    except Exception as e:
        log_struct('ERROR', 'Webhook processing failed', error=str(e))
        return {"statusCode": 500, "body": "Internal error"}


def verify_signature(data, signature):
    """Verifies the incoming Billplz webhook X-Signature."""
    if not signature:
        log_struct('DEBUG', 'No signature provided')
        return False
    
    # Billplz signature verification keys in order
    keys = ['amount', 'collection_id', 'due_at', 'email', 'id', 'mobile', 'name', 
            'paid_amount', 'paid_at', 'paid', 'state', 'url']
    
    # Build string to sign
    to_sign = "|".join([f"{k}{data.get(k, '')}" for k in keys])
    
    # Get appropriate signature key based on collection
    collection_id = data.get('collection_id')
    if collection_id == JPJ_COLLECTION_ID:
        key = JPJ_BILLPLZ_X_SIGNATURE_KEY
    elif collection_id == TNB_COLLECTION_ID:
        key = TNB_BILLPLZ_X_SIGNATURE_KEY
    else:
        log_struct('DEBUG', 'Unknown collection ID', collectionId=collection_id, availableIds=[JPJ_COLLECTION_ID, TNB_COLLECTION_ID])
        return False
    
    # Generate signature
    expected = hmac.new(
        key.encode('utf-8'),
        to_sign.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    log_struct('DEBUG', 'Signature verification', toSign=to_sign, expected=expected, received=signature)
    
    return hmac.compare_digest(expected, signature)