INSERT INTO po_testing.runs AS stored (run_id, payload)
VALUES (($1::jsonb)->>'run_id', $1::jsonb)
ON CONFLICT (run_id) DO UPDATE SET payload = stored.payload
RETURNING run_id,
  CASE WHEN payload = $1::jsonb THEN 'stored' ELSE 'conflict' END AS storage_status;
