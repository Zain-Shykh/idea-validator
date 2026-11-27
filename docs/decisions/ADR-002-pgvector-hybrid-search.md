# ADR-002: pgvector in Postgres with hybrid search and reranking

- **Status:** Accepted (retrieval numbers to be filled after the 20-query evaluation)
- **Date:** 2026-11-27
- **Owner:** Ahmad (M6)
- **Context:** The KB needs hybrid search with reranking, filters by date/source/niche and card ids for citations (M6-2..4). The Guide's figure shows a separate vector store; the laptop has ~15 GB RAM and little free disk.
- **Options:** (1) Qdrant as a separate vector DB + Postgres for cards; (2) ChromaDB + BM25 in Python; (3) pgvector in the same Postgres instance (database `kb`): vector top-50 (HNSW cosine) + Postgres full-text top-50, fused with Reciprocal Rank Fusion, reranked by a small ONNX cross-encoder.
- **Decision:** Option 3. "Vector store" = the `kb` database with pgvector; one Postgres StatefulSet hosts `app`, `kb`, `ops` databases.
- **Why:** One SQL query does vectors, keywords and filters together; cards, chunks and vectors stay transactionally consistent (idempotent ingestion is simple upserts); one fewer stateful service to run, back up and secure. Expected: hybrid+rerank beats vector-only on exact product names (to be confirmed with recall@5 / MRR on our ≥ 20 queries).
- **Cost:** Postgres full-text is weaker than a tuned BM25; HNSW index build time grows with cards (fine at ~2k–10k); KB and app share one Postgres pod (resource contention, single point of failure in dev).
- **AI use:** Options listed by AI; decision made by us based on disk/RAM limits.
