import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { createGlobalStyle } from 'styled-components';
import { Jupyter, JupyterLabApp, JupyterLabCorePlugins } from '@datalayer/jupyter-react';
import { JupyterLab } from '@jupyterlab/application';
import * as collaborationExtension from '@jupyter/collaboration-extension';
import * as clouderExtension from './jupyterlab/index';
import Clouder from './Clouder';

const { extensionPromises, mimeExtensionPromises } = JupyterLabCorePlugins;

const ThemeGlobalStyle = createGlobalStyle<any>`
  body {
    background-color: white !important;
  }
`

const ClouderJupyterLabHeadless = () => {
  const [jupyterLab, setJupyterLab] = useState<JupyterLab>();
  const onReady = (jupyterLab: JupyterLab) => {
    setJupyterLab(jupyterLab);
  }
  return (
    <>
      {jupyterLab && <Clouder app={jupyterLab}/>}
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
