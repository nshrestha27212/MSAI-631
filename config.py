# config.py
import os

class DefaultConfig:
    """Bot Configuration"""
    PORT = 3978
    APP_ID = os.environ.get("MicrosoftAppId", "")
    APP_PASSWORD = os.environ.get("MicrosoftAppPassword", "")
    API_Key = os.environ.get("MicrosoftAPIKey", "D34Rgp5Tf84Kj6skhbPd9ii43GDvRZkhVEf1SF7zhkYNnsQOpfQqJQQJ99BJACrJL3JXJ3w3AAAaACOG1c0h")
    ENDPOINT_URI = os.environ.get("MicrosoftAIServiceEndpoint", "https://msai631language.cognitiveservices.azure.com/")
