import { cloudflareLogin } from './cloudflare-login';
import type { BrowserAuth } from './provider';

/** The active browser authentication provider. Swap the import to replace it. */
export const browserAuth: BrowserAuth = cloudflareLogin;

export type { BrowserAuth, SignInCredential, SignInProps } from './provider';
