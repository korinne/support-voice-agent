/** Display helpers shared by the customer account panel and the staff desk. */

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

export function money(cents: number) {
  return usd.format(cents / 100);
}

export function capitalize(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// Clef scores urgency from 0 to 3 against these four levels, most relaxed first.
const urgencyLevels = [
  'Within two business days',
  'Within one business day',
  'Today',
  'Right away',
];

export function urgency(score: number) {
  const level = urgencyLevels[Math.min(3, Math.max(0, Math.round(score)))];
  return `${level} (${score.toFixed(1)} of 3)`;
}

// Clef answers "did the customer ask for a person?" with a probability. Below
// this, the ticket's wording was ambiguous, so the UI doesn't claim it.
export const WANTS_PERSON = 0.7;

export function percent(probability: number) {
  return `${Math.round(probability * 100)}%`;
}

const dateTime = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
});
const dateOnly = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
});

/** Formats a stored timestamp in the viewer's time zone, or a plain date as written. */
export function when(value: string) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return dateOnly.format(new Date(`${value}T00:00:00Z`));
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : dateTime.format(parsed);
}
