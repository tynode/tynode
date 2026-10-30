import os

os.environ["TYNODE_DATABASE_URL"] = "sqlite:///:memory:"
os.environ.pop("TYNODE_API_KEY", None)