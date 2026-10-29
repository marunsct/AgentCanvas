/** A form field: its label (wrapping the control, so a click on the words focuses it), a hint under it and, when
 * something is wrong, the error in words, announced when it appears. */
export function Field({ label, hint, error, children }: {
  label: React.ReactNode; hint?: React.ReactNode; error?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="field">
      <label>
        {label}
        {children}
      </label>
      {hint && <p className="muted small field-hint">{hint}</p>}
      {error && <p className="error-text small field-error" role="alert">{error}</p>}
    </div>
  );
}
