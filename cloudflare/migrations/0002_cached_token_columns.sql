ALTER TABLE usage_events ADD COLUMN cached_input_tokens INTEGER;
ALTER TABLE usage_events ADD COLUMN non_cached_input_tokens INTEGER;
ALTER TABLE usage_events ADD COLUMN reasoning_output_tokens INTEGER;
