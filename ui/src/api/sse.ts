export type SseEventHandler = (event: MessageEvent, source: EventSource) => void;

export interface SseSubscription {
  close: () => void;
}

export interface SseOptions {
  handlers: Record<string, SseEventHandler>;
  onError?: (source: EventSource) => void;
  withCredentials?: boolean;
}

interface Subscriber {
  handlers: Record<string, SseEventHandler>;
  onError?: (source: EventSource) => void;
}

interface Channel {
  source: EventSource;
  subscribers: Map<SseSubscription, Subscriber>;
  boundNames: Set<string>;
}

const channels = new Map<string, Channel>();

function teardown(key: string): void {
  const channel = channels.get(key);
  if (!channel) return;
  channel.source.close();
  channels.delete(key);
}

export function subscribeSse(url: string, options: SseOptions): SseSubscription {
  const withCredentials = options.withCredentials ?? false;
  const key = withCredentials ? `${url}#cred` : url;
  let channel = channels.get(key);
  if (!channel) {
    channel = {
      source: new EventSource(url, withCredentials ? { withCredentials: true } : undefined),
      subscribers: new Map(),
      boundNames: new Set(),
    };
    channels.set(key, channel);
  }
  const channelRef = channel;
  const subscription: SseSubscription = {
    close: () => {
      if (!channelRef.subscribers.has(subscription)) return;
      channelRef.subscribers.delete(subscription);
      if (channelRef.subscribers.size === 0) teardown(key);
    },
  };
  channel.subscribers.set(subscription, { handlers: options.handlers, onError: options.onError });
  for (const name of Object.keys(options.handlers)) {
    if (channelRef.boundNames.has(name)) continue;
    channelRef.boundNames.add(name);
    channelRef.source.addEventListener(
      name,
      ((evt: MessageEvent) => {
        for (const subscriber of channelRef.subscribers.values()) {
          const handler = subscriber.handlers[name];
          if (handler) {
            try { handler(evt, channelRef.source); } catch { /* shield other subscribers */ }
          }
        }
      }) as EventListener,
    );
  }
  channelRef.source.onerror = () => {
    for (const subscriber of channelRef.subscribers.values()) {
      if (subscriber.onError) {
        try { subscriber.onError(channelRef.source); } catch { /* shield other subscribers */ }
      }
    }
  };
  return subscription;
}
