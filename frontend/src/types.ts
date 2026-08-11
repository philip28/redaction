export type JobStatus =
  | "pending"
  | "analyzing"
  | "ready"
  | "redacting"
  | "complete"
  | "error";

export interface DocumentInfo {
  id: string;
  filename: string;
  size: number;
  output_name: string | null;
  replacements: number;
  error: string | null;
}

export interface EntityInfo {
  id: string;
  category: string;
  value: string;
  variants: string[];
  source: "llm" | "rule";
  inflect: boolean;
  selected: boolean;
  occurrences: Record<string, number>;
  total_occurrences: number;
  tag: string | null;
}

export interface Job {
  id: string;
  kind: "anonymize" | "deanonymize";
  status: JobStatus;
  created_at: number;
  documents: DocumentInfo[];
  entities: EntityInfo[];
  warnings: string[];
  error: string | null;
  unmapped_tags: string[];
}

export interface ServerConfig {
  categories: string[];
  detectors: string[];
  inflect_defaults: string[];
  max_upload_mb: number;
  max_files_per_job: number;
  job_ttl_minutes: number;
  llm_configured: boolean;
  llm_model: string | null;
  tag_prefix: string;
  tag_suffix: string;
  supported_extensions: string[];
}

export interface EntityPatch {
  id: string;
  selected?: boolean;
  inflect?: boolean;
  variants?: string[];
  category?: string;
}
