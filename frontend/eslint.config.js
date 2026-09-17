// Flat config for ESLint 9 — substrate baseline (CI-substrate task).
// Uses only packages already in devDependencies. Rules that flag existing
// scaffold style are set to "warn": the lint job fails on errors only.
const js = require("@eslint/js");
const react = require("eslint-plugin-react");
const reactHooks = require("eslint-plugin-react-hooks");
const globals = require("globals");

module.exports = [
  {
    ignores: ["build/**", "dist/**", "node_modules/**", "coverage/**"],
  },
  js.configs.recommended,
  {
    // Scaffold Node scripts (webpack plugin) run in CJS with Node globals.
    files: ["plugins/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: { ...globals.node },
    },
  },
  {
    files: ["src/**/*.{js,jsx}", "*.config.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: {
      react,
      "react-hooks": reactHooks,
    },
    settings: { react: { version: "detect" } },
    rules: {
      ...react.configs.recommended.rules,
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react/prop-types": "off",
      // React 19 + react-scripts 5 / Vite both use the automatic JSX runtime —
      // importing React for scope is neither needed nor practiced here.
      "react/react-in-jsx-scope": "off",
      // Pre-existing scaffold style; src edits are out of scope for this task.
      "react/no-unescaped-entities": "warn",
      "react/no-unknown-property": "warn",
      "no-unused-vars": "warn",
    },
  },
];
