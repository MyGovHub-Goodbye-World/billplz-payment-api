# Billplz Payment API

Serverless payment service for MyGovHub that integrates with Billplz payment gateway and MongoDB for transaction management.

© 2025 Goodbye World team, for Great AI Hackathon Malaysia 2025 usage.

## API Endpoints

### Create Payment Bill
**POST** `/payment/create-bill`

Creates a transaction in MongoDB and generates a Billplz payment URL.

**Request:**
```json
{
    "user_id": "040218-07-0711",
    "service_type": "frontend-test",
    "description": "attempt 2",
    "amount": 50.00,
    "email": "test@example.com",
    "name": "MyGovHub App Test",
    "metadata": {
        "sessionId": "session_abc123",
        "renewalYears": 2,
        "licenseNumber": "12345678"
    },
    "api_key": "<BILLPLZ_API_KEY>",
    "collection_id": "<BILLPLZ_COLLECTION_ID>"
}
```

**Response:**
```json
{
    "url": "https://www.billplz-sandbox.com/bills/81d6b4ad18675bc7"
}
```

### Payment Webhook
**POST** `/payment/webhook`

Receives payment status updates from Billplz and updates MongoDB transaction status.

## MongoDB Actions

1. **Transaction Creation**: Creates initial transaction document with `pending` status
2. **Billplz Integration**: Updates transaction with Billplz bill ID and payment URL
3. **Status Updates**: Updates transaction status to `paid` or `failed` via webhook

## Environment Variables

```bash
MONGODB_MCP_URL=<mongodb_connection_string>
DB_NAME=<database_name>
COLLECTION_NAME=<collection_name>
JPJ_COLLECTION_ID=<jpj_collection_id>
TNB_COLLECTION_ID=<tnb_collection_id>
JPJ_BILLPLZ_X_SIGNATURE_KEY=<jpj_signature_key>
TNB_BILLPLZ_X_SIGNATURE_KEY=<tnb_signature_key>
CALLBACK_URL=<webhook_callback_url>
REDIRECT_URL=<payment_redirect_url>
```

## Deployment

```bash
npm install
serverless deploy
```