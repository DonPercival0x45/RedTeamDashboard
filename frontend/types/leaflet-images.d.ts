// v2.21.0: leaflet's default marker icons ship as PNGs inside its package.
// Next.js's next-env.d.ts declares `*.png` at build time, but standalone
// `tsc --noEmit` (our CI typecheck step) doesn't always pick that up.
// Declare the specific paths we import so both toolchains agree.

declare module "leaflet/dist/images/marker-icon-2x.png" {
  const src: string;
  export default src;
}
declare module "leaflet/dist/images/marker-icon.png" {
  const src: string;
  export default src;
}
declare module "leaflet/dist/images/marker-shadow.png" {
  const src: string;
  export default src;
}
