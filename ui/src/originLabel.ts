/**
 * Friendly display label for a message's originating surface.
 *
 * Only messages whose `origin` differs from `"web"` (this UI's own surface)
 * get a badge, so unknown surface ids render verbatim rather than being
 * silently dropped.
 */
export function originLabel(origin: string): string {
  switch (origin) {
    case "chrome-extension":
      return "Chrome";
    case "ios":
      return "iOS";
    case "telegram":
      return "Telegram";
    case "web":
      return "Web";
    default:
      return origin;
  }
}
