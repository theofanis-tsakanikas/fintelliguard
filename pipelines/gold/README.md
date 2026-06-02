# pipelines/gold/

**Gold** DLT tables — the **15 fraud features** consumed by ML.

Feature parity is non-negotiable: these exact features train the model (`ml/training/`)
and serve at inference (`ml/features/` adapters). Any feature change touches gold +
training + both adapters in the **same commit**. Tables: `gold.<name>`. See
`@docs/features.md`.
