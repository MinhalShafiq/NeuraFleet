import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'

export default [
  { ignores: ['dist', 'node_modules'] },
  js.configs.recommended,
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { react, 'react-hooks': reactHooks },
    settings: { react: { version: 'detect' } },
    rules: {
      ...react.configs.recommended.rules,
      // Classic hook rules only: the preset's React-Compiler rules (set-state-in-effect, etc.)
      // flag patterns used deliberately here and need a refactor of their own.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react/react-in-jsx-scope': 'off', // new JSX transform
      'react/prop-types': 'off', // no PropTypes in this codebase
      // react-three-fiber elements (<points>, <pointsMaterial>, <ambientLight>...) take three.js props
      'react/no-unknown-property': [
        'error',
        {
          ignore: [
            'args',
            'attach',
            'position',
            'rotation',
            'roughness',
            'metalness',
            'intensity',
            'geometry',
            'frustumCulled',
            'vertexColors',
            'sizeAttenuation',
            'transparent',
            'count',
            'itemSize',
            'array',
          ],
        },
      ],
      'no-unused-vars': ['error', { varsIgnorePattern: '^_', argsIgnorePattern: '^_' }],
    },
  },
]
