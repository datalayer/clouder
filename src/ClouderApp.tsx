/// <reference types="webpack-env" />

import { createRoot } from 'react-dom/client';
import ClouderJupyterLabHeadless from './ClouderJupyterLabHeadless';

const div = document.createElement('div');
document.body.appendChild(div);
const root = createRoot(div);

if (module.hot) {
  module.hot.accept('./ClouderJupyterLabHeadless', () => {
    const ClouderJupyterLabHeadless = require('./ClouderJupyterLabHeadless').default;
    root.render(<ClouderJupyterLabHeadless/>);
  })
}

root.render(<ClouderJupyterLabHeadless/>);
