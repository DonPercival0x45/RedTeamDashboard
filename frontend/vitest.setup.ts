// Vitest global setup — runs once before the test suite.
//
// - Registers @testing-library/jest-dom matchers (toBeInTheDocument, …)
//   so component assertions read naturally.
// - Polyfills matchMedia / ResizeObserver / IntersectionObserver which
//   jsdom omits but several Radix UI primitives touch on mount.
// - Centralises global mock cleanup so individual tests stay focused.
import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

// Radix UI (Dialog, Label, Slot) reads matchMedia on mount.
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// IntersectionObserver is used by some lazy lists; jsdom lacks it.
if (!("IntersectionObserver" in globalThis)) {
  class IO {
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  }
  Object.defineProperty(globalThis, "IntersectionObserver", {
    writable: true,
    value: IO,
  });
}

// ResizeObserver is touched by recharts/chart layouts on mount.
if (!("ResizeObserver" in globalThis)) {
  Object.defineProperty(globalThis, "ResizeObserver", {
    writable: true,
    value: class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
}

// jsdom has no native scrollTo; components that run on mount shouldn't throw.
if (!window.scrollTo) {
  window.scrollTo = () => {};
}

// next/navigation mocks — many components call useSearchParams/useRouter
// at module top level. Tests that need real navigation override these.
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: () => {},
    replace: () => {},
    back: () => {},
    refresh: () => {},
    prefetch: () => {},
  }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
  notFound: () => {
    throw new Error("notFound()");
  },
  redirect: (href: string) => {
    throw new Error(`redirect(${href})`);
  },
}));
