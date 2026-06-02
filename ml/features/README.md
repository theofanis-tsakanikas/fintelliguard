# ml/features/

Mosaic AI **Feature Store** definitions + adapters that produce the **15 Gold features**.

- `adapter_ieee.py` — maps the IEEE-CIS training columns → the 15 features.
- `adapter_stream.py` — maps live stream transactions → the same 15 features.

Both adapters must stay in lockstep so training and serving see identical features.
Any feature change updates these **and** `ml/training/` in the same commit.
See `@docs/features.md`.
