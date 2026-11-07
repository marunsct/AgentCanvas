{{- define "agentcanvas.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "agentcanvas.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "agentcanvas.selector" -}}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* No image is published: build both, push them to your registry and name them in image.*.repository (and tag). */}}
{{- define "agentcanvas.image" -}}
{{- $img := index .root.Values.image .component -}}
{{- $repo := required (printf "image.%s.repository is required: build the %s image, push it to your registry and set its repository (and image.%s.tag, default the chart's appVersion %s); see docs/user-guide/deploy.md" .component .component .component .root.Chart.AppVersion) $img.repository -}}
{{ $repo }}:{{ $img.tag | default .root.Chart.AppVersion }}
{{- end -}}

{{/* The keys the Secret must hold for the values given. */}}
{{- define "agentcanvas.secretKeys" -}}
{{- $keys := list "DATABASE_URL" "AGENTCANVAS_SECRET_KEY" "LANGGRAPH_AES_KEY" -}}
{{- if .Values.sandbox.enabled }}{{ $keys = append $keys "AGENTCANVAS_SANDBOX_TOKEN" }}{{ end -}}
{{- if and .Values.sandbox.enabled .Values.sandbox.egress.enabled }}{{ $keys = append $keys "AGENTCANVAS_EGRESS_SECRET" }}{{ end -}}
{{ join " " $keys }}
{{- end -}}

{{/* The Secret's name. When the cluster can be read (helm install/upgrade; not helm template), a Secret that is there
but lacks a required key fails the release instead of pods that crash-loop or never start. */}}
{{- define "agentcanvas.secret" -}}
{{- $keys := include "agentcanvas.secretKeys" . -}}
{{- $name := required (printf "secrets.existingSecret is required: a Secret with %s (see values.yaml)" $keys) .Values.secrets.existingSecret -}}
{{- $found := lookup "v1" "Secret" .Release.Namespace $name -}}
{{- if $found -}}
{{- range $key := splitList " " $keys -}}
{{- if not (hasKey (default dict $found.data) $key) -}}
{{- fail (printf "Secret %s has no %s (it needs %s)" $name $key $keys) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{ $name }}
{{- end -}}

{{/* config.authMode, checked: oidc (any OpenID Connect provider) or matura (sign-in, members and roles from Matura). */}}
{{- define "agentcanvas.authMode" -}}
{{- $mode := .Values.config.authMode | default "oidc" -}}
{{- if not (has $mode (list "oidc" "matura")) -}}
{{- fail (printf "config.authMode must be oidc or matura, not %q (dev mode, no sign-in, is refused in staging and prod)" $mode) -}}
{{- end -}}
{{ $mode }}
{{- end -}}

{{/* config.scriptExecution, checked: remote needs the sandbox service this chart runs. */}}
{{- define "agentcanvas.scriptExecution" -}}
{{- $mode := .Values.config.scriptExecution -}}
{{- if and (eq $mode "remote") (not .Values.sandbox.enabled) -}}
{{- fail "config.scriptExecution=remote runs workspace code in the sandbox service: set sandbox.enabled=true, or choose docker or langsmith (subprocess and inprocess are refused in staging and prod unless AGENTCANVAS_ALLOW_UNISOLATED_SCRIPTS=true)" -}}
{{- end -}}
{{ $mode }}
{{- end -}}

{{/* Environment shared by the API pods and the migration job. */}}
{{- define "agentcanvas.apiEnv" -}}
- name: AGENTCANVAS_ENV
  value: {{ .Values.config.env | quote }}
- name: AGENTCANVAS_AUTO_MIGRATE
  value: "false"
- name: AGENTCANVAS_AUTH_MODE
  value: {{ include "agentcanvas.authMode" . | quote }}
- name: AGENTCANVAS_OIDC_ISSUER
  value: {{ required "config.oidc.issuer is required" .Values.config.oidc.issuer | quote }}
- name: AGENTCANVAS_OIDC_AUDIENCE
  value: {{ required "config.oidc.audience is required" .Values.config.oidc.audience | quote }}
- name: AGENTCANVAS_OIDC_CLIENT_ID
  value: {{ .Values.config.oidc.clientId | quote }}
- name: AGENTCANVAS_OIDC_SCOPES
  value: {{ .Values.config.oidc.scopes | quote }}
- name: AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS
  value: {{ .Values.config.bootstrapAdminEmails | toJson | quote }}
- name: AGENTCANVAS_CORS_ORIGINS
  value: {{ .Values.config.corsOrigins | toJson | quote }}
{{- if eq (include "agentcanvas.authMode" .) "matura" }}
{{- with .Values.config.matura }}
{{- if .url }}
- name: AGENTCANVAS_MATURA_URL
  value: {{ .url | quote }}
{{- end }}
{{- if .publicUrl }}
- name: AGENTCANVAS_MATURA_PUBLIC_URL
  value: {{ .publicUrl | quote }}
{{- end }}
{{- if .portalUrl }}
- name: AGENTCANVAS_MATURA_PORTAL_URL
  value: {{ .portalUrl | quote }}
{{- end }}
{{- if .auditUrl }}
- name: AGENTCANVAS_MATURA_AUDIT_URL
  value: {{ .auditUrl | quote }}
{{- end }}
{{- if .syncIntervalS }}
- name: AGENTCANVAS_MATURA_SYNC_INTERVAL_S
  value: {{ .syncIntervalS | quote }}
{{- end }}
{{- end }}
{{- end }}
- name: AGENTCANVAS_SCRIPT_EXECUTION
  value: {{ include "agentcanvas.scriptExecution" . | quote }}
{{- if eq .Values.config.scriptExecution "remote" }}
- name: AGENTCANVAS_SANDBOX_URL
  value: {{ printf "http://%s-sandbox:8090" (include "agentcanvas.fullname" .) | quote }}
{{- if .Values.sandbox.egress.enabled }}
- name: AGENTCANVAS_EGRESS_PROXY
  value: {{ printf "%s-egress:3128" (include "agentcanvas.fullname" .) | quote }}
# the API hands the sandbox the proxy's ClusterIP, not its name: the sandbox pods get no DNS (no names to leak through)
- name: AGENTCANVAS_EGRESS_PROXY_RESOLVE
  value: "true"
{{- end }}
{{- end }}
- name: FORWARDED_ALLOW_IPS
  value: {{ .Values.config.forwardedAllowIps | quote }}
- name: AGENTCANVAS_METRICS_ENABLED
  value: {{ .Values.observability.metrics.enabled | quote }}
{{- with .Values.observability.otlpEndpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ . | quote }}
- name: OTEL_SERVICE_NAME
  value: agentcanvas-api
{{- end }}
{{- range $name, $value := .Values.config.extraEnv }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- end -}}

{{/* Spread a component's pods across nodes (a node lost takes one, not all); ScheduleAnyway keeps a one-node
cluster working. Call with (dict "root" . "component" "api"). */}}
{{- define "agentcanvas.spread" -}}
{{- $component := .component -}}
{{- $root := .root -}}
{{- with .root.Values.topologySpread }}
{{- if .enabled }}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: {{ .topologyKey }}
    whenUnsatisfiable: {{ .whenUnsatisfiable }}
    labelSelector:
      matchLabels:
        {{- include "agentcanvas.selector" (dict "root" $root "component" $component) | nindent 8 }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "agentcanvas.podSecurity" -}}
runAsNonRoot: true
seccompProfile: { type: RuntimeDefault }
{{- end -}}

{{- define "agentcanvas.containerSecurity" -}}
allowPrivilegeEscalation: false
capabilities: { drop: [ALL] }
{{- end -}}
