import os

# config.py refuses to import without these; the tests never talk to slack or the model
for _name in ("SLACK_USER_TOKEN", "SLACK_APP_TOKEN", "USER_ID", "OPENAI_API_KEY"):
    os.environ.setdefault(_name, "test")
