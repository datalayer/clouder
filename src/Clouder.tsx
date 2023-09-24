import { useState, useEffect } from 'react';
import { ThemeProvider, BaseStyles, Box } from '@primer/react';
import { UnderlineNav } from '@primer/react/drafts';
import { ThemeProvider as BrandThemeProvider } from '@primer/react-brand'
import { CloudGreyIcon, OvhCloudIcon, AwsIcon } from '@datalayer/icons-react';
import { JupyterFrontEnd } from '@jupyterlab/application';
import { observer } from 'mobx-react';
import { requestAPI } from './jupyterlab/handler';
import ClouderTab from './tabs/ClouderTab';
import OVHTab from './tabs/OVHTab';
import AWSTab from './tabs/AWSTab';
import AboutTab from './tabs/AboutTab';
import appState from "./state";

import '@primer/react-brand/lib/css/main.css';

export type ClouderProps = {
  app?: JupyterFrontEnd;
}

const Clouder = observer((props: ClouderProps): JSX.Element => {
  const [version, setVersion] = useState('');
  useEffect(() => {
    requestAPI<any>('config')
    .then(data => {
      setVersion(data.version);
    })
    .catch(reason => {
      console.error(
        `Error while accessing the jupyter server clouder extension.\n${reason}`
      );
    });
  });
  return (
    <>
      <BrandThemeProvider>
        <ThemeProvider>
          <BaseStyles>
            <Box className="datalayer-Primer-Brand">
              <Box>
                <UnderlineNav aria-label="clouder">
                  <UnderlineNav.Item aria-label="clouder" aria-current={appState.tab === 1 ? "page" : undefined} onSelect={e => {e.preventDefault(); appState.setTab(1);}}>
                    Clouder
                  </UnderlineNav.Item>
                  <UnderlineNav.Item aria-label="ovh-cloud" aria-current={appState.tab === 2 ? "page" : undefined} icon={OvhCloudIcon} onSelect={e => {e.preventDefault(); appState.setTab(2);}}>
                    OVHcloud
                  </UnderlineNav.Item>
                  <UnderlineNav.Item aria-label="aws" aria-current={appState.tab === 3 ? "page" : undefined} icon={AwsIcon} onSelect={e => {e.preventDefault(); appState.setTab(3);}}>
                    AWS
                  </UnderlineNav.Item>
                  <UnderlineNav.Item aria-label="about" aria-current={appState.tab === 4 ? "page" : undefined} icon={CloudGreyIcon} onSelect={e => {e.preventDefault(); appState.setTab(4);}}>
                    About
                  </UnderlineNav.Item>
                </UnderlineNav>
              </Box>
              <Box m={3}>
                {(appState.tab === 1) && <ClouderTab/>}
                {(appState.tab === 2) && <OVHTab/>}
                {(appState.tab === 3) && <AWSTab/>}
                {(appState.tab === 4) && <AboutTab version={version}/>}
              </Box>
            </Box>
          </BaseStyles>
        </ThemeProvider>
      </BrandThemeProvider>
    </>
  );
});

export default Clouder;
