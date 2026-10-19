/** Where the keyboard focus goes back to when the port menu closes: the port that opened it. */
let opener: HTMLElement | null = null;

export function rememberOpener(el: HTMLElement | null): void {
  opener = el;
}

/** Whether the menu was opened from the keyboard (it then takes the focus; a mouse click leaves it alone). */
export function openedByKeyboard(): boolean {
  return opener !== null;
}

/** Give the focus back to the port that opened the menu, if it is still on the page. */
export function restoreOpener(): void {
  if (opener?.isConnected) opener.focus();
  opener = null;
}
