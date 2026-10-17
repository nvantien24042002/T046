import js from '@eslint/js'
import ts from 'typescript-eslint'
import hooks from 'eslint-plugin-react-hooks'
import a11y from 'eslint-plugin-jsx-a11y'
import globals from 'globals'
export default ts.config(
 { ignores: ['dist/**', 'node_modules/**', '.cache/**'] },
 js.configs.recommended, ...ts.configs.recommended,
 { files: ['**/*.{ts,tsx}'], languageOptions: { globals: { ...globals.browser, ...globals.node } }, plugins: { 'react-hooks': hooks, 'jsx-a11y': a11y }, rules: { ...hooks.configs.recommended.rules, ...a11y.configs.recommended.rules } },
 { files: ['*.js'], languageOptions: { globals: globals.node } },
)
