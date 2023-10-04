import { useState } from 'react';
import { createGlobalStyle } from 'styled-components';
import { JupyterLab } from '@jupyterlab/application';
import { Jupyter, JupyterLabApp, JupyterLabAppAdapter } from '@datalayer/jupyter-react';
import Clouder from './Clouder';

import * as lightThemeExtension from '@jupyterlab/theme-light-extension';
import * as collaborationExtension from '@jupyter/collaboration-extension';
import * as clouderExtension from './jupyterlab/index';

const ThemeGlobalStyle = createGlobalStyle<any>`
  body {
    background-color: white !important;
  }
`

const ClouderJupyterLabHeadlessComponent = () => {
  const [jupyterlab, setJupyterlab] = useState<JupyterLab>();
  const onReady = (jupyterlabAdapter: JupyterLabAppAdapter) => {
    setJupyterlab(jupyterlabAdapter.jupyterlab);
  }
  return (
    <>
      {jupyterlab && <Clouder jupyterFrontEnd={jupyterlab}/>}
      <JupyterLabApp
        extensions={[
          lightThemeExtension,
          clouderExtension,
          collaborationExtension,
        ]}
        headless={true}
        onReady={onReady}
      />
    </>
  )
}

export const ClouderJupyterLabHeadless = () => (
  <Jupyter startDefaultKernel={false} disableCssLoading={true}>
    <ThemeGlobalStyle />
    <ClouderJupyterLabHeadlessComponent/>
  </Jupyter>
);

export default ClouderJupyterLabHeadless;
