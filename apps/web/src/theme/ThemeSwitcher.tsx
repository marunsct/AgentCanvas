/** Light / Medium / Dark / System, as a compact segmented control in the top bar. */
import { THEMES, useTheme } from "./theme";

const ICON: Record<string, React.ReactNode> = {
  light: <><circle cx="8" cy="8" r="3" /><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" /></>,
  medium: <><circle cx="8" cy="8" r="5.5" /><path d="M8 2.5a5.5 5.5 0 0 1 0 11z" fill="currentColor" /></>,
  dark: <path d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z" />,
  system: <><rect x="2" y="3" width="12" height="8.5" rx="1.5" /><path d="M6 14h4M8 11.5V14" /></>,
};

export function ThemeSwitcher() {
  const theme = useTheme((s) => s.theme);
  const setTheme = useTheme((s) => s.setTheme);
  return (
    <div className="theme-switch" role="radiogroup" aria-label="Colour theme" data-testid="theme-switch">
      {THEMES.map((t) => (
        <button type="button" key={t.id} role="radio" aria-checked={theme === t.id} title={`${t.label} theme`}
          aria-label={`${t.label} theme`} className={theme === t.id ? "active" : ""} onClick={() => setTheme(t.id)}>
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{ICON[t.id]}</svg>
        </button>
      ))}
    </div>
  );
}
