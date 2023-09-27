import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createGlobalStyle } from 'styled-components';
import { JupyterLab } from '@jupyterlab/application';
import * as collaborationExtension from '@jupyter/collaboration-extension';
import { Jupyter, JupyterLabApp, JupyterLabAppAdapter, JupyterLabAppCorePlugins } from '@datalayer/jupyter-react';
import * as clouderExtension from './jupyterlab/index';
import Clouder from './Clouder';

const { extensionPromises, mimeExtensionPromises } = JupyterLabAppCorePlugins;

const ThemeGlobalStyle = createGlobalStyle<any>`
  body {
    background-color: white !important;
  }
`

const ClouderJupyterLabHeadless = () => {
  const [jupyterlab, setJupyterLab] = useState<JupyterLab>();
  const onReady = (jupyterlabAdapter: JupyterLabAppAdapter) => {
    setJupyterLab(jupyterlabAdapter.jupyterlab);
  }
  return (
    <>
      {jupyterlab && <Clouder app={jupyterlab}/>}
      <JupyterLabApp
        extensions={[
          clouderExtension,
          collaborationExtension,
        ]}
        extensionPromises={extensionPromises}
        mimeExtensionPromises={mimeExtensionPromises}
        hostId="clouder-jupyterlab-id"
        headless={true}
        onReady={onReady}
      />
    </>
  )
}

const div = document.createElement('div');
document.body.appendChild(div);
const root = createRoot(div);

root.render(
  <Jupyter startDefaultKernel={false} disableCssLoading={true}>
    <ThemeGlobalStyle />
    <ClouderJupyterLabHeadless/>
  </Jupyter>
);
