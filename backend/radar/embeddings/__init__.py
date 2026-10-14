"""Local embeddings and the vector index (SPEC §9). Owner: emb.

One embedding space per model; never compare vectors across models (C3). This package never imports
``radar.net``, ``radar.connectors``, ``radar.ingestion``, ``radar.jobs`` or ``radar.graph.expand``.

Modules (import from them directly; this package re-exports nothing so importing it stays cheap):

* ``base`` - ``Embedder`` protocol, ``paper_text``, ``content_hash`` (+ vector byte helpers).
* ``registry`` - ``get_embedder``, ``available_embedders``, ``fallback_active``, ``reset``,
  ``model_cached``, ``known_dimension``, the ``download_model`` job ``run_download_model``.
* ``vectors`` - ``ensure_paper_vectors``, ``ensure_vectors``, ``get_vectors``, ``delete_vectors`` and
  ``store_vectors`` (write vectors computed elsewhere; used by tests with synthetic models).
* ``index`` - ``VectorIndex`` (paper vectors, per model, lazy, invalidated after vector commits).
* ``hashing`` / ``fastembed_backend`` - the two embedders (``local/hashing-v1``,
  ``fastembed/BAAI/bge-small-en-v1.5``).

Usage: ``emb = get_embedder(project_model_or_None)``; pass ``emb.model_id`` to every vector call.
"""
