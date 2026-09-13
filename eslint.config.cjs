const js = require('@eslint/js');
const globals = require('globals');

module.exports = [
  {
    ...js.configs.recommended,
    files: ['skills/**/*.js', 'skills/**/*.cjs', 'eslint.config.cjs'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: globals.node,
    },
  },
  {
    files: ['skills/pdf/scripts/_render_html.cjs'],
    languageOptions: { globals: globals.browser },
  },
];
