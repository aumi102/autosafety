# 05 — Agent Workflow

## Recommended implementation

Use LangGraph or a small custom state machine. The first implementation can be custom Python; keep the state shape compatible with LangGraph so migration is easy.

## State object

```python
class AgentState(TypedDict):
    run_id: str
    question: str
    intent: Literal["sql", "graph_rag", "hybrid", "safety", "clarification"]
    entities: dict
    plan: list[dict]
    sql_query: str | None
    sql_result: dict | None
    graph_paths: list[dict]
    retrieved_chunks: list[dict]
    citations: list[dict]
    warnings: list[str]
    final_answer: dict | None
    latency_ms: int | None
    tool_calls: list[dict]
```

## Workflow nodes

```text
1. classify_question
2. extract_entities
3. plan_tools
4. generate_sql
5. validate_sql
6. execute_sql
7. graph_retrieve
8. synthesize_answer
9. confidence_check
10. persist_trace
11. return_response
```

## Routing rules

### SQL route

Choose when the question contains:

```text
top, count, compare, trend, increase, decrease, most, least, by year, by make, by component, how many
```

### GraphRAG route

Choose when the question contains:

```text
remedy, consequence, summarize, narrative, investigation, communication, technical service bulletin, campaign number, evidence
```

### Hybrid route

Choose when the question asks for both pattern discovery and evidence lookup.

Examples:

```text
Find vehicles with many steering complaints and check related recalls.
Which component had the most complaints, and what official evidence exists?
Show trend and summarize recall/investigation evidence.
```

### Safety route

Choose when user asks for:

```text
DROP, DELETE, UPDATE, INSERT, ALTER, TRUNCATE, CREATE, COPY, GRANT, REVOKE
personal information
unsupported legal/safety conclusion
```

## SQL generation constraints

The SQL generator receives only:

```text
- read-only schema views
- approved column descriptions
- examples
- SQL policy
```

It must output:

```json
{
  "sql": "SELECT ... LIMIT 100",
  "purpose": "...",
  "expected_columns": ["..."],
  "assumptions": ["..."]
}
```

## SQL validator rules

Block unless all are true:

```text
statement type is SELECT or WITH
no semicolon chaining
no write keywords
no system catalogs unless explicitly allowed
limit <= 500
timeout <= 10 seconds
only approved schemas/views
```

## GraphRAG retrieval strategy

Run three retrievers and merge results:

### 1. Entity retriever

Extract and resolve:

```text
make, model, model_year, component, campaign_number, odi_number, investigation_number, communication_number
```

### 2. Graph neighborhood retriever

Get 1–2 hop neighborhood around resolved entities.

### 3. Semantic retriever

Search embedded chunks from:

```text
complaints.summary
complaints.narrative
recalls.summary
recalls.consequence
recalls.remedy
investigations.summary
manufacturer_communications.summary
```

## Answer synthesis rules

The answer composer must output:

1. direct answer,
2. SQL summary if SQL was used,
3. evidence summary if retrieval was used,
4. citations,
5. graph paths,
6. warnings/caveats,
7. confidence.

## Confidence logic

Start with `medium`, then adjust:

```text
+ SQL executed successfully and result non-empty
+ cited source records exactly match entities
+ graph path relation_source is source_record
- entity ambiguity
- only semantic_similarity relation found
- no recalls/investigations found
- missing model year or component
- source record text is sparse
```

## Mandatory caveat

For complaint-based trend or association answers, include:

```text
Complaint volume alone does not prove a safety defect or official causality. This answer summarizes public records and possible associations.
```
