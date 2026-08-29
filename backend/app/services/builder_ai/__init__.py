"""AI-assisted Connector Builder.

The package may read product state and propose validated change sets. It never
publishes a connector or writes a BuilderDefinition directly. Imports stay
lazy so deterministic parsing and ChangeSet validation do not require the
optional provider SDK to be imported.
"""
