#!/usr/bin/env python3
"""Buy a US Twilio number and make a test call."""
import os
from twilio.rest import Client

account_sid = os.environ["TWILIO_ACCOUNT_SID"]
auth_token = os.environ["TWILIO_AUTH_TOKEN"]
client = Client(account_sid, auth_token)

# Check account balance first
balance = client.api.v2010.balance.fetch()
print(f"Account balance: {balance.balance} {balance.currency}")

# Search for available US local numbers
print("\nSearching for available US numbers...")
numbers = client.available_phone_numbers("US").local.list(
    voice_enabled=True,
    sms_enabled=True,
    limit=5,
)

for n in numbers:
    print(f"  {n.phone_number} - {n.friendly_name} ({n.locality}, {n.region})")

if not numbers:
    print("No numbers found!")
    exit(1)

# Buy the first one
chosen = numbers[0].phone_number
print(f"\nBuying {chosen}...")
purchased = client.incoming_phone_numbers.create(phone_number=chosen)
print(f"Purchased: {purchased.phone_number} (SID: {purchased.sid})")
print(f"Capabilities: voice={purchased.capabilities.get('voice')}, sms={purchased.capabilities.get('sms')}")
