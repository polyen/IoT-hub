import { formatDistanceToNow, format, parseISO, type Locale } from "date-fns";
import { uk, enUS } from "date-fns/locale";

let currentLocale: Locale = uk;

export function setLocale(lang: string) {
  currentLocale = lang === "en" ? enUS : uk;
}

export function relativeTime(iso: string): string {
  return formatDistanceToNow(parseISO(iso), { addSuffix: true, locale: currentLocale });
}

export function shortDateTime(iso: string): string {
  return format(parseISO(iso), "dd.MM HH:mm", { locale: currentLocale });
}

export function fullDateTime(iso: string): string {
  return format(parseISO(iso), "dd MMM yyyy, HH:mm:ss", { locale: currentLocale });
}

export function countdownSeconds(expiresAt: string): number {
  return Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000));
}
