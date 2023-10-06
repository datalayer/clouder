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
  const [jupyterLab, setJupyterLab] = useState<JupyterLab>();
  const onJupyterLab = (jupyterlabAdapter: JupyterLabAppAdapter) => {
    setJupyterLab(jupyterlabAdapter.jupyterLab);
  }
  return (
    <>
      {jupyterLab && <Clouder jupyterFrontend={jupyterLab}/>}
      <JupyterLabApp
        extensions={[
          lightThemeExtension,
          clouderExtension,
          collaborationExtension,
        ]}
        headless={true}
        onJupyterLab={onJupyterLab}
      />
    </>
  )
}

export const ClouderJupyterLabHeadless = () => (
  <Jupyter startDefaultKernel={false} disableCssLoading={true} collaborative={true}>
    <ThemeGlobalStyle />
    <ClouderJupyterLabHeadlessComponent/>
  </Jupyter>
);

export default ClouderJupyterLabHeadless;
