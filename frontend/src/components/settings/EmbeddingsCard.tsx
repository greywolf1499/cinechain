import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import Toast, { type ToastState } from "../Toast";
import { SettingsCard, inputClass } from "./shared";
import { ApiError, api } from "../../lib/api";
import type { EmbeddingProvider, EmbeddingTestResult, IntegrationConfig } from "../../types/api";

const CONFIG_KEY = ["settings", "integrations"] as const;

const PROVIDERS: { value: EmbeddingProvider; label: string; detail: string }[] = [
  {
    value: "local_onnx",
    label: "Local ONNX",
    detail: "all-MiniLM-L6-v2 runs inside CineChain (a ~23 MB model is downloaded on first use). No setup.",
  },
  {
    value: "ollama",
    label: "Ollama",
    detail: "A local Ollama server. Pull a model first, e.g. `ollama pull nomic-embed-text`.",
  },
  {
    value: "openai",
    label: "OpenAI-compatible",
    detail: "Any /v1/embeddings endpoint: OpenAI, LM Studio, vLLM, LiteLLM, OpenRouter...",
  },
];

const PLACEHOLDERS: Record<EmbeddingProvider, { url: string; model: string }> = {
  local_onnx: { url: "", model: "" },
  ollama: { url: "http://localhost:11434", model: "nomic-embed-text" },
  openai: { url: "https://api.openai.com/v1", model: "text-embedding-3-small" },
};

const labelClass = "flex flex-col gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500";

/** Admin panel for the Semantic Trope Web's embedding provider, with a Test Connection button. */
export default function EmbeddingsCard() {
  const queryClient = useQueryClient();
  const { data: config } = useQuery({
    queryKey: CONFIG_KEY,
    queryFn: () => api.get<IntegrationConfig>("/settings/integrations"),
  });
  const [toast, setToast] = useState<ToastState | null>(null);
  const [provider, setProvider] = useState<EmbeddingProvider>("local_onnx");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [result, setResult] = useState<EmbeddingTestResult | null>(null);

  useEffect(() => {
    if (!config) return;
    setProvider(config.embedding_provider);
    setBaseUrl(config.embedding_base_url);
    setModel(config.embedding_model);
  }, [config]);

  const external = provider !== "local_onnx";
  const placeholders = PLACEHOLDERS[provider];

  const test = useMutation({
    mutationFn: () =>
      api.post<EmbeddingTestResult>("/settings/integrations/test-embeddings", {
        embedding_provider: provider,
        embedding_base_url: baseUrl.trim(),
        embedding_model: model.trim(),
        embedding_api_key: apiKey.trim() || undefined,
      }),
    onSuccess: setResult,
    onError: (err) =>
      setToast({ type: "error", message: err instanceof ApiError ? err.message : "The test failed." }),
  });

  const save = useMutation({
    // An empty string clears the stored value, so switching back to local drops stale URLs.
    mutationFn: () =>
      api.patch<IntegrationConfig>("/settings/integrations", {
        embedding_provider: provider,
        embedding_base_url: external ? baseUrl.trim() : "",
        embedding_model: external ? model.trim() : "",
        ...(provider === "openai" && apiKey.trim() ? { embedding_api_key: apiKey.trim() } : {}),
        ...(provider !== "openai" ? { embedding_api_key: "" } : {}),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(CONFIG_KEY, updated);
      setApiKey("");
      setToast({ type: "success", message: "Embedding settings saved." });
    },
    onError: (err) =>
      setToast({ type: "error", message: err instanceof ApiError ? err.message : "Failed to save settings." }),
  });

  return (
    <SettingsCard title="AI & Embeddings">
      <div className="flex flex-col gap-4 px-5 py-4">
        <p className="text-xs text-zinc-600">
          The Semantic Trope Web compares film plots as text embeddings. Pick where they are computed. If an
          external provider is slow (5s) or unreachable, CineChain quietly falls back to the local model.
          Switching providers re-embeds films as they come up, since vectors from different models can't be
          compared.
        </p>

        <div role="radiogroup" aria-label="Embedding provider" className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {PROVIDERS.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={provider === option.value}
              onClick={() => {
                setProvider(option.value);
                setResult(null);
              }}
              className={`flex flex-col gap-1 rounded-lg border px-3 py-2.5 text-left transition-colors ${
                provider === option.value
                  ? "border-accent bg-accent/10"
                  : "border-app-border hover:border-zinc-600"
              }`}
            >
              <span className={`text-sm font-semibold ${provider === option.value ? "text-accent" : "text-zinc-200"}`}>
                {option.label}
              </span>
              <span className="text-[11px] leading-snug text-zinc-500">{option.detail}</span>
            </button>
          ))}
        </div>

        {external && (
          <div className="flex flex-col gap-3 sm:flex-row">
            <label className={`${labelClass} flex-1`}>
              Base URL
              <input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={placeholders.url}
                className={`${inputClass} normal-case`}
              />
            </label>
            <label className={`${labelClass} flex-1`}>
              Model
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={placeholders.model}
                className={`${inputClass} normal-case`}
              />
            </label>
          </div>
        )}

        {provider === "openai" && (
          <label className={labelClass}>
            API Key
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                config?.embedding_api_key_masked ? `Currently set: ${config.embedding_api_key_masked}` : "sk-..."
              }
              autoComplete="off"
              className={`${inputClass} normal-case`}
            />
          </label>
        )}

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={save.isPending}
            onClick={() => save.mutate()}
            className="flex items-center gap-1.5 rounded-md bg-accent px-3.5 py-2 text-sm font-semibold text-zinc-950 transition-colors hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
          >
            {save.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Save
          </button>
          <button
            type="button"
            disabled={test.isPending}
            onClick={() => {
              setResult(null);
              test.mutate();
            }}
            className="flex items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
          >
            {test.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            Test Connection
          </button>
          {result && (
            <span
              role="status"
              className={`flex items-center gap-1.5 text-xs ${result.ok ? "text-emerald-400" : "text-red-400"}`}
            >
              {result.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
              {result.ok
                ? `Connected - ${result.model}: ${result.dimension}-dimension vectors in ${result.latency_ms} ms`
                : (result.detail ?? "Failed")}
            </span>
          )}
        </div>
      </div>
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </SettingsCard>
  );
}
