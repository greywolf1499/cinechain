import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import Toast, { type ToastState } from "../Toast";
import { SettingsCard, inputClass } from "./shared";
import { ApiError, api } from "../../lib/api";
import type {
  EmbeddingProvider,
  EmbeddingTestResult,
  IntegrationConfig,
  LlmProvider,
  LlmTestResult,
  LocalPresetInfo,
  LocalPresetKey,
} from "../../types/api";

const CONFIG_KEY = ["settings", "integrations"] as const;

const PROVIDERS: { value: EmbeddingProvider; label: string; detail: string }[] = [
  {
    value: "local_onnx",
    label: "Local ONNX",
    detail: "A small INT8 model runs inside CineChain, downloaded on first use. Pick the preset below. No setup.",
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
  const [preset, setPreset] = useState<LocalPresetKey>("arctic-embed-xs");
  const [result, setResult] = useState<EmbeddingTestResult | null>(null);

  useEffect(() => {
    if (!config) return;
    setProvider(config.embedding_provider);
    setPreset(config.embedding_local_preset);
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
        embedding_local_preset: preset,
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
        embedding_local_preset: preset,
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

        {!external && (
          <LocalPresetPicker
            presets={config?.embedding_local_presets ?? []}
            value={preset}
            onChange={(next) => {
              setPreset(next);
              setResult(null);
            }}
          />
        )}

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
      <LlmSection config={config} onToast={setToast} />
      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </SettingsCard>
  );
}

const LLM_PROVIDERS: { value: LlmProvider; label: string; detail: string }[] = [
  { value: "off", label: "Off", detail: "No generative features. Nothing is downloaded or loaded." },
  {
    value: "local_gguf",
    label: "Local Qwen 0.8B",
    detail: "Qwen3.5-0.8B-Instruct (Q4_K_M GGUF, ~530 MB) via llama-cpp-python, downloaded on first use.",
  },
  {
    value: "ollama",
    label: "Ollama",
    detail: "A local Ollama server, e.g. `ollama pull qwen3.5:0.8b`.",
  },
  {
    value: "openai",
    label: "OpenAI-compatible",
    detail: "Any /v1/chat/completions endpoint: LM Studio, vLLM, LiteLLM, OpenRouter...",
  },
];

const LLM_PLACEHOLDERS: Record<LlmProvider, { url: string; model: string }> = {
  off: { url: "", model: "" },
  local_gguf: { url: "", model: "Qwen3.5-0.8B-Q4_K_M.gguf" },
  ollama: { url: "http://localhost:11434", model: "qwen3.5:0.8b" },
  openai: { url: "http://localhost:1234", model: "qwen3.5-0.8b" },
};

const KEEP_ALIVE_OPTIONS = [
  { value: 0, label: "Unload immediately after each generation" },
  { value: 300, label: "Unload after 5 minutes idle (default)" },
  { value: 900, label: "Unload after 15 minutes idle" },
];

/** The opt-in generative model behind "Why this link?" pitches and cryptic Blind Draft teasers. */
/** The "Local ONNX Model Preset" dropdown, with a card explaining the selected model's strengths. */
function LocalPresetPicker({
  presets,
  value,
  onChange,
}: {
  presets: LocalPresetInfo[];
  value: LocalPresetKey;
  onChange: (next: LocalPresetKey) => void;
}) {
  const selected = presets.find((p) => p.key === value);
  return (
    <div className="flex flex-col gap-2">
      <label className={labelClass}>
        Local ONNX Model Preset
        <select
          value={value}
          onChange={(e) => onChange(e.target.value as LocalPresetKey)}
          disabled={presets.length === 0}
          className={`${inputClass} normal-case`}
        >
          {presets.map((preset) => (
            <option key={preset.key} value={preset.key}>
              {preset.label} {preset.badge}
              {preset.recommended ? " (Recommended)" : ""}
            </option>
          ))}
        </select>
      </label>
      {selected && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-app-border bg-app-bg/60 px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-semibold text-accent">
              {selected.badge}
            </span>
            {selected.recommended && (
              <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
                Recommended
              </span>
            )}
            <span
              className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                selected.downloaded ? "bg-sky-500/15 text-sky-300" : "bg-app-surface-hover text-zinc-400"
              }`}
            >
              {selected.downloaded ? "Downloaded" : `Downloads ~${selected.size_mb}MB on first use`}
            </span>
          </div>
          <p className="text-[11px] leading-snug text-zinc-500">
            {selected.description} All presets produce 384-dimension vectors from{" "}
            <span className="font-mono">{selected.hf_repo}</span>.
          </p>
          <p className="text-[11px] leading-snug text-zinc-600">
            Switching preset re-embeds films as they come up: vectors from different models can't be compared.
          </p>
        </div>
      )}
    </div>
  );
}

function LlmSection({
  config,
  onToast,
}: {
  config: IntegrationConfig | undefined;
  onToast: (toast: ToastState) => void;
}) {
  const queryClient = useQueryClient();
  const [provider, setProvider] = useState<LlmProvider>("off");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [keepAlive, setKeepAlive] = useState(300);
  const [result, setResult] = useState<LlmTestResult | null>(null);

  useEffect(() => {
    if (!config) return;
    setProvider(config.llm_provider);
    setBaseUrl(config.llm_base_url);
    setModel(config.llm_model);
    setKeepAlive(config.llm_keep_alive_seconds);
  }, [config]);

  const remote = provider === "ollama" || provider === "openai";
  const placeholders = LLM_PLACEHOLDERS[provider];

  const test = useMutation({
    mutationFn: () =>
      api.post<LlmTestResult>("/settings/integrations/test-llm", {
        llm_provider: provider,
        llm_base_url: remote ? baseUrl.trim() : "",
        llm_model: model.trim(),
        llm_keep_alive_seconds: keepAlive,
        llm_api_key: apiKey.trim() || undefined,
      }),
    onSuccess: setResult,
    onError: (err) =>
      onToast({ type: "error", message: err instanceof ApiError ? err.message : "The test failed." }),
  });

  const save = useMutation({
    // An empty string clears the stored value, so switching provider drops stale URLs and keys.
    mutationFn: () =>
      api.patch<IntegrationConfig>("/settings/integrations", {
        llm_provider: provider,
        llm_base_url: remote ? baseUrl.trim() : "",
        llm_model: provider === "off" ? "" : model.trim(),
        llm_keep_alive_seconds: keepAlive,
        ...(provider === "openai" && apiKey.trim() ? { llm_api_key: apiKey.trim() } : {}),
        ...(provider !== "openai" ? { llm_api_key: "" } : {}),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(CONFIG_KEY, updated);
      queryClient.invalidateQueries({ queryKey: ["engine", "llm-status"] });
      setApiKey("");
      onToast({ type: "success", message: "Generative model settings saved." });
    },
    onError: (err) =>
      onToast({ type: "error", message: err instanceof ApiError ? err.message : "Failed to save settings." }),
  });

  return (
    <div className="flex flex-col gap-4 border-t border-app-border px-5 py-4">
      <div>
        <h3 className="text-sm font-medium text-zinc-300">Generative Model (LLM)</h3>
        <p className="mt-1 rounded-md border border-fuchsia-400/20 bg-fuchsia-500/5 px-3 py-2 text-xs leading-relaxed text-fuchsia-200/90">
          💡 Opt-In Local LLM: Enables connection pitches and cryptic teasers. Peaks at ~600MB RAM during
          generation, auto-unloads when idle.
        </p>
      </div>

      <div role="radiogroup" aria-label="Generative model provider" className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {LLM_PROVIDERS.map((option) => (
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
              provider === option.value ? "border-fuchsia-400 bg-fuchsia-500/10" : "border-app-border hover:border-zinc-600"
            }`}
          >
            <span className={`text-sm font-semibold ${provider === option.value ? "text-fuchsia-300" : "text-zinc-200"}`}>
              {option.label}
            </span>
            <span className="text-[11px] leading-snug text-zinc-500">{option.detail}</span>
          </button>
        ))}
      </div>

      {provider === "local_gguf" && !config?.llm_local_available && (
        <p role="alert" className="rounded-md border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-300">
          llama-cpp-python isn&apos;t installed on this server (<code>pip install llama-cpp-python</code>). Install it
          to run the model in-process, or choose Ollama / OpenAI-compatible instead.
        </p>
      )}

      {provider !== "off" && (
        <div className="flex flex-col gap-3 sm:flex-row">
          {remote && (
            <label className={`${labelClass} flex-1`}>
              Base URL
              <input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={placeholders.url}
                className={`${inputClass} normal-case`}
              />
            </label>
          )}
          <label className={`${labelClass} flex-1`}>
            {provider === "local_gguf" ? "GGUF file (optional)" : "Model"}
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
            placeholder={config?.llm_api_key_masked ? `Currently set: ${config.llm_api_key_masked}` : "Optional"}
            autoComplete="off"
            className={`${inputClass} normal-case`}
          />
        </label>
      )}

      {provider === "local_gguf" && (
        <label className={labelClass}>
          Memory
          <select
            value={keepAlive}
            onChange={(e) => setKeepAlive(Number(e.target.value))}
            className={`${inputClass} normal-case`}
          >
            {KEEP_ALIVE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
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
          disabled={test.isPending || provider === "off"}
          onClick={() => {
            setResult(null);
            test.mutate();
          }}
          className="flex items-center gap-1.5 rounded-md border border-app-border px-3.5 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-app-surface-hover disabled:cursor-not-allowed disabled:opacity-60"
        >
          {test.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Test LLM Generation
        </button>
        {provider === "local_gguf" && test.isPending && (
          <span className="text-xs text-zinc-500">First run downloads ~530 MB and loads the model...</span>
        )}
      </div>
      {result && (
        <div
          role="status"
          className={`flex items-start gap-1.5 text-xs ${result.ok ? "text-emerald-400" : "text-red-400"}`}
        >
          {result.ok ? (
            <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          ) : (
            <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          )}
          <span>
            {result.ok
              ? `${result.model} answered in ${result.latency_ms} ms: "${result.output}"`
              : (result.detail ?? "Failed")}
          </span>
        </div>
      )}
    </div>
  );
}
