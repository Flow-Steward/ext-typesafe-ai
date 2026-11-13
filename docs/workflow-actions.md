# Workflow actions

Save a TypeSafe Console API key in a project TypeSafe JEV connection and select it on the step. Configure its default model (initially jev-latest). Test TypeSafe authentication lists accessible models without sending a classification request.

For support routing, supply a state object containing the customer message and a named Choice question. Read answers.category.choice and its confidence in a later routing step. Noul returns a yes probability, whereas Choice/Score confidence measures confidence in that answer; provider credits apply to evaluation.

Select the named action in an Extension tools step. Map workflow values to its inputs and map the returned fields needed by later steps. Examples below contain sample data; replace artifact handles and embedding values with actual earlier-step outputs.

## Classify or score state with JEV

Send text or structured state and named Choice, Score or Noul questions through a TypeSafe connection. Returns answer objects, model and usage for later routing steps; an optional workflow secret overrides the connection key for this call.

- **State to evaluate (JSON)**: Supply text as a JSON string, or a JSON object or array containing the evidence to classify. The total provider request must fit within 2 MiB.
- **Named decision questions (JSON object)**: Provide 1–256 named questions. Each needs type and instructions. Choice criteria are an object of labels; Score criteria are 2–10 ordered levels; Noul optionally describes true/false criteria. Keys become keys in answers.
- **Model override**: Optional model name. Leave blank to use the selected connection default; pin a model version when routing thresholds must remain stable.
- **Workflow secret override**: Optional advanced mapping from a workflow input of type secret. Leave blank to use the encrypted connection key. Map the authorized secret reference directly; do not paste a literal key.

Example input values:

```json
{
  "state": {
    "message": "I need help with a duplicate charge."
  },
  "questions": {
    "category": {
      "type": "choice",
      "instructions": "Classify the customer request.",
      "criteria": {
        "billing": "Payments, invoices or refunds",
        "technical": "Product problems",
        "general": "Other requests"
      }
    }
  },
  "model": "jev-latest"
}
```

## Test TypeSafe authentication and list models

Authenticate using the selected TypeSafe connection and list accessible JEV models. Does not send state or make a classification request.


Uses the selected project connection; no additional action inputs.
