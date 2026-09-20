TELEGRAM AUTO POST BOT

1. Python 3.10+ ইনস্টল করুন।
2. Terminal/CMD খুলে এই folder-এ যান।
3. চালান:
   pip install -r requirements.txt

4. bot.py-এর উপরের 3টি সেটিং পরিবর্তন করুন:
   BOT_TOKEN
   CHANNEL_USERNAME
   ADMIN_ID

5. Telegram Channel-এ Bot-কে Administrator করুন।
6. চালান:
   python bot.py

ব্যবহার:
ছবি পাঠান এবং caption লিখুন:
06:00|সুপ্রভাত 🌅

Bot প্রতিদিন Bangladesh Time (UTC+6) অনুযায়ী ওই সময়ে ছবিটি Channel-এ পোস্ট করবে।

Commands:
/start
/help
/list
/delete ID
/clear
/on
/off
