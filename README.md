# MyGovHub Payment Service (aws-payment)
## Overview
This is a serverless microservice responsible for handling all payment-related processing for the MyGovHub project. It acts as a secure backend layer that integrates with the Billplz payment gateway.

The primary role of this service is to create payment bills and securely handle payment status updates, keeping sensitive API keys off the frontend client and the main aws-brain (MCP) service.

This service is built using the Serverless Framework and deployed on AWS Lambda and API Gateway.

## Architecture
The payment flow is designed to be secure and decoupled from the main chatbot logic: 
1. The `aws-brain` (MCP) service determines that a payment is required.
2. `aws-brain` calls the `/payment/create-bill` endpoint of this service with payment details.
3. This service securely retrieves the Billplz API Key and Collection ID from environment variables.
4. It calls the Billplz API to create a new bill.
5. It returns the unique payment URL to `aws-brain`.
6. Billplz sends a webhook to the `/payment/webhook` endpoint of this service upon successful payment to update the transaction status.

## API Endpoints
1. Create a Bill
- Endpoint: `POST /payment/create-bill`
- Description: Creates a new payment bill via the Billplz API.
- Request Body (`application/json`):
```json
{
  "amount": 3000,
  "description": "License Renewal for 1 year",
  "email": "user@example.com",
  "name": "John Doe",
  "redirect_url": "[https://yourapp.com/payment-success](https://yourapp.com/payment-success)"
}
```
  - `amount`: The total amount in cents (e.g., `3000` for RM 30.00).

- Success Response (`200 OK`):
```json
{
  "url": "[https://www.billplz-sandbox.com/bills/xxxxxxxx](https://www.billplz-sandbox.com/bills/xxxxxxxx)"
}
```

2. Handle Payment Webhook
- Endpoint: `POST /payment/webhook`
- Description: Receives payment status updates from Billplz after a user completes a transaction. This endpoint is intended to be called only by Billplz.
- TODO: The logic for verifying the webhook's X-Signature and updating the main database needs to be implemented.

## Prerequisites
Before you can deploy or test this service, you need the following tools installed and configured:
1. Node.js & npm
2. Serverless Framework: npm install -g serverless
3. AWS CLI: [Installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
4. Configured AWS Credentials: Run `aws configure` and provide your IAM user's credentials.

Setup and Deployment
1. Install Dependencies
This service uses the `serverless-python-requirements` plugin to manage Python packages. The plugin itself is a Node.js dependency.
```bash
# From within the aws-payment directory
npm install
```
2. Configure Environment Variables
Create a `.env` file in the root of the `aws-payment` directory. You can copy the example file:
```bash
cp .env.example .env
```
Now, open the `.env` file and add your secret credentials from the Billplz Sandbox.

File: `.env`
```bash
BILLPLZ_API_KEY="YOUR_SANDBOX_API_KEY_HERE"
BILLPLZ_COLLECTION_ID="YOUR_COLLECTION_ID_HERE"
```
Note: The `.env` file is listed in `.gitignore` and will not be committed to the repository.

3. Deploy to AWS
Run the deploy command from the `aws-payment` directory:

```bash
serverless deploy
```
The Serverless Framework will automatically read the variables from your .env file and securely deploy them as environment variables to your Lambda function. Upon successful deployment, the API Gateway endpoints will be displayed in your terminal.

## Testing the Service
After a successful deployment, you can test the createBill endpoint directly using a tool like cURL.

1. Using cURL

Replace the URL with the one you received from the deployment output.
```bash
curl -X POST \
  'https://<your-api-gateway-id>[.execute-api.us-east-1.amazonaws.com/dev/payment/create-bill](https://.execute-api.us-east-1.amazonaws.com/dev/payment/create-bill)' \
  --header 'Content-Type: application/json' \
  --data '{
    "amount": 150,
    "description": "Test from README",
    "email": "test@example.com",
    "name": "Test User"
  }'
```
2. Checking Logs

If you encounter an "Internal Server Error" or other issues, you can view the live logs for your function directly from your terminal:
```bash
# Make sure you are in the aws-payment directory
serverless logs -f createBill --tail
```