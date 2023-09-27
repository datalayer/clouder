import { createRoot } from 'react-dom/client';
import { Jupyter, JupyterLabApp, JupyterLabAppCorePlugins } from '@datalayer/jupyter-react';
import * as collaborationExtension from '@jupyter/collaboration-extension';
import * as clouderExtension from './jupyterlab/index';

const { extensionPromises, mimeExtensionPromises } = JupyterLabAppCorePlugins;

const ClouderJupyterLab = () => (
  <JupyterLabApp
    extensions={[
      clouderExtension,
      collaborationExtension,
    ]}
    extensionPromises={extensionPromises}
    mimeExtensionPromises={mimeExtensionPromises}
    hostId="jupyterlab-clouder-id"
    position="absolute"
    height="100vh"
  />
)

const div = document.createElement('div');
document.body.appendChild(div);
const root = createRoot(div);

root.render(
  <Jupyter startDefaultKernel={false} disableCssLoading={true}>
    <ClouderJupyterLab/>
  </Jupyter>
);
