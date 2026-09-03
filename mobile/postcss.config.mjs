// Tailwind v4 is a PostCSS plugin and nothing else -- no tailwind.config.js,
// no content globs. The theme lives in app/globals.css under @theme.
// Same setup as site/, deliberately: two Tailwind installs in one repo that
// are configured differently is a trap.
export default {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
