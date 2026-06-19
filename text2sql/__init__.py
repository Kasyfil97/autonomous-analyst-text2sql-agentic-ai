"""Text-to-SQL agent package.

A CLI agent that turns a natural-language question into a draft SQL query (no
execution), grounded in the ERA-precedent and schema knowledge bases, driven by
gpt-oss-120b on Bedrock via the federated session in ``bedrock_session.py``.
"""
