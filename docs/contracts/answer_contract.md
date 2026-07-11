# Answer Contract

All chat responses must conform to this shape.

```json
{
  "run_id": "uuid",
  "intent": "sql | graph_rag | hybrid | safety | clarification",
  "answer": {
    "summary": "string",
    "sections": [
      {
        "title": "string",
        "content": "string",
        "type": "text | table_summary | evidence_summary | caveat"
      }
    ]
  },
  "sql": {
    "used": true,
    "query": "SELECT ...",
    "columns": ["component", "complaint_count"],
    "rows": [],
    "row_count": 0,
    "execution_ms": 0,
    "validated": true
  },
  "evidence": {
    "citations": [
      {
        "source_type": "recall",
        "source_id": "uuid",
        "source_key": "22V176000",
        "field_name": "remedy",
        "text_span": "short cited span or field summary",
        "confidence": 0.9
      }
    ],
    "graph_paths": [
      {
        "path_text": "Ford F-150 2022 -> SERVICE BRAKES -> Recall 22V...",
        "relation_source": "source_record | normalized_join | semantic_similarity",
        "confidence": 0.85
      }
    ]
  },
  "warnings": [
    "Complaint volume alone does not prove a safety defect or official causality."
  ],
  "confidence": {
    "label": "low | medium | high",
    "score": 0.0,
    "reasons": ["string"]
  },
  "debug": {
    "tool_call_count": 0,
    "latency_ms": 0
  }
}
```

## Required behavior

### SQL-only answer

```text
sql.used = true
evidence.citations can be empty
warnings included when complaint analytics are used
```

### GraphRAG-only answer

```text
sql.used = false
evidence.citations must be non-empty unless no source found
```

### Hybrid answer

```text
sql.used = true
evidence.citations should be non-empty
evidence.graph_paths should be non-empty when graph retrieval succeeds
```

### Safety refusal

```text
intent = safety
sql.used = false
answer.summary explains refusal
warnings explain allowed safe alternative
```
