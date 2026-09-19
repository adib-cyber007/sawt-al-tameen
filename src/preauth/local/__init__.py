"""Local execution mode: a fully offline voice/text channel onto the same application layer.

This package is an *adapter*, just like the hosted provider adapters. It owns speech
recognition, speech synthesis, a local tool-calling model, conversation state and a browser UI. It owns no
insurance logic whatsoever: every fact it speaks comes back from the same three agent tools, the same rules
engine and the same recommendation engine that the hosted AssemblyAI agent calls.
"""
