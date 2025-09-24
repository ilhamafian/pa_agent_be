# WhatsApp Token Configuration Guide

## Problem

Your WhatsApp Business API access token has expired. The error shows:

```
Error validating access token: Session has expired on Monday, 22-Sep-25 02:00:00 PDT. The current time is Wednesday, 24-Sep-25 04:17:08 PDT.
```

## Solution

I've implemented automatic token refresh functionality. You need to add the following environment variables to your `.env` file:

### Required Environment Variables

Add these to your `.env` file:

```env
# WhatsApp Business API Configuration
WHATSAPP_TOKEN=your_current_access_token
WHATSAPP_APP_ID=your_facebook_app_id
WHATSAPP_APP_SECRET=your_facebook_app_secret
PHONE_NUMBER_ID=your_phone_number_id
VERIFY_TOKEN=your_webhook_verify_token
```

### How to Get These Values

1. **WHATSAPP_TOKEN**: Your current access token (this will be automatically refreshed)
2. **WHATSAPP_APP_ID**: Found in your Facebook App settings
3. **WHATSAPP_APP_SECRET**: Found in your Facebook App settings
4. **PHONE_NUMBER_ID**: Your WhatsApp Business phone number ID
5. **VERIFY_TOKEN**: A custom string you set for webhook verification

### Facebook App Setup

1. Go to [Facebook Developers](https://developers.facebook.com/)
2. Select your WhatsApp Business API app
3. Go to App Settings > Basic
4. Copy the App ID and App Secret
5. Go to WhatsApp > API Setup
6. Copy the Phone Number ID and Access Token

### What the Fix Does

The updated code now:

1. **Detects Token Expiration**: Automatically detects when the token expires (401 error with code 190)
2. **Refreshes Token**: Uses your App ID and App Secret to get a new access token
3. **Retries Request**: Automatically retries the failed WhatsApp message with the new token
4. **Updates Global Token**: Updates the in-memory token for future requests

### Testing

After adding the environment variables, restart your application. The system will now automatically handle token expiration without manual intervention.

### Manual Token Refresh

If you need to manually refresh the token, you can call the refresh function:

```python
from utils.utils import refresh_whatsapp_token
new_token = await refresh_whatsapp_token()
```

## Notes

- WhatsApp tokens typically expire after 24 hours
- The refresh mechanism uses the `client_credentials` grant type
- Make sure your Facebook App has the necessary permissions for WhatsApp Business API
- The system will log token refresh attempts for debugging
