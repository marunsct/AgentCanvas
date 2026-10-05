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

{{- define "agentcanvas.image" -}}
{{- $img := index .root.Values.image .component -}}
{{ $img.repository }}:{{ $img.tag | default .root.Chart.AppVersion }}
{{- end -}}

{{- define "agentcanvas.secret" -}}
{{- required "secrets.existingSecret is required: a Secret with DATABASE_URL and AGENTCANVAS_SECRET_KEY" .Values.secrets.existingSecret -}}
{{- end -}}

{{/* Environment shared by the API pods and the migration job. */}}
{{- define "agentcanvas.apiEnv" -}}
- name: AGENTCANVAS_ENV
  value: {{ .Values.config.env | quote }}
- name: AGENTCANVAS_AUTO_MIGRATE
  value: "false"
- name: AGENTCANVAS_AUTH_MODE
  value: "oidc"
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
- name: AGENTCANVAS_SCRIPT_EXECUTION
  value: {{ .Values.config.scriptExecution | quote }}
- name: FORWARDED_ALLOW_IPS
  value: {{ .Values.config.forwardedAllowIps | quote }}
{{- range $name, $value := .Values.config.extraEnv }}
- name: {{ $name }}
  value: {{ $value | quote }}
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
