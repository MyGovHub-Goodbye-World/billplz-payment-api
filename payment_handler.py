import json
import os
import requests

# Secrets will now come directly from environment variables
BILLPLZ_API_KEY = os.environ.get("BILLPLZ_API_KEY")
BILLPLZ_COLLECTION_ID = os.environ.get("BILLPLZ_COLLECTION_ID")
BILLPLZ_API_URL = "https://www.billplz-sandbox.com/api/v3/bills"

def create_bill(event, context):
    """Creates a Billplz bill and returns the payment URL."""
    try:
        body = json.loads(event.get('body', '{}'))
        amount = body.get('amount')
        description = body.get('description')
        email = body.get('email')
        name = body.get('name')

        if not all([amount, description, email, name, BILLPLZ_API_KEY, BILLPLZ_COLLECTION_ID]):
            return {"statusCode": 400, "body": json.dumps({"error": "Missing required parameters or environment variables."})}

        payload = {
            'collection_id': BILLPLZ_COLLECTION_ID,
            'email': email,
            'name': name,
            'amount': int(amount), # Amount in cents
            'description': description,
            'callback_url': "http://your-callback-url.com/not-used-for-webhooks",
            'redirect_url': body.get('redirect_url')
        }

        response = requests.post(
            BILLPLZ_API_URL,
            data=payload,
            auth=(BILLPLZ_API_KEY, '')
        )
        response.raise_for_status()
        bill_data = response.json()

        return {"statusCode": 200, "body": json.dumps({"url": bill_data.get("url")})}
    except Exception as e:
        print(f"Error creating bill: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

def handle_webhook(event, context):
    """Handles incoming webhooks from Billplz."""
    print(f"Webhook received: {event.get('body')}")
    # Your webhook logic here...
    return {"statusCode": 200, "body": "OK"}