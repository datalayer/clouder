import { useState } from 'react';
import { JupyterLab } from '@jupyterlab/application';
import { Jupyter, JupyterLabApp, JupyterLabAppAdapter } from '@datalayer/jupyter-react';

import * as lightThemeExtension from '@jupyterlab/theme-light-extension';
import * as collaborationExtension from '@jupyter/collaboration-extension';
import * as clouderExtension from './jupyterlab/index';

const ClouderJupyterLabComponent = () => {
  const [jupyterlab, setJupyterLab] = useState<JupyterLab>();
  const onReady = (jupyterlabAdapter: JupyterLabAppAdapter) => {
    setJupyterLab(jupyterlabAdapter.jupyterlab);
  }
  return (
    <>
      {jupyterlab && <></>}
      <JupyterLabApp
        extensions={[
          lightThemeExtension,
          collaborationExtension,
          clouderExtension,
        ]}
        position="absolute"
        height="100vh"
        onReady={onReady}
      />
    </>
  )
}

export const ClouderJupyterLab = () => (
  <Jupyter startDefaultKernel={false} disableCssLoading={true}>
    <ClouderJupyterLabComponent/>
  </Jupyter>
);

export default ClouderJupyterLab;
