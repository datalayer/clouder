import { useState, useEffect } from 'react';
import { ThemeProvider, BaseStyles, Box } from '@primer/react';
import { UnderlineNav } from '@primer/react/drafts';
import { ThemeProvider as BrandThemeProvider } from '@primer/react-brand'
import { CloudGreyIcon, OvhCloudIcon, AwsIcon } from '@datalayer/icons-react';
import { JupyterFrontEnd } from '@jupyterlab/application';
import { observer } from 'mobx-react';
import { requestAPI } from './jupyterlab/handler';
import ClouderTab from './tabs/ClouderTab';
import OVHcloudTab from './tabs/OVHcloudTab';
import AWSTab from './tabs/AWSTab';
import AboutTab from './tabs/AboutTab';
import appState from "./state";

import '@primer/react-brand/lib/css/main.css';

export type ClouderProps = {
  jupyterFrontEnd?: JupyterFrontEnd;
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
  const tabIndex = Math.floor(appState.tab)
  return (
    <>
      <BrandThemeProvider>
        <ThemeProvider>
          <BaseStyles>
            <Box className="datalayer-Primer-Brand">
              <Box>
                <UnderlineNav aria-label="clouder">
                  <UnderlineNav.Item aria-label="clouder" aria-current={tabIndex === 0 ? "page" : undefined} onSelect={e => {e.preventDefault(); appState.setTab(0.0);}}>
                    Clouder
                  </UnderlineNav.Item>
                  <UnderlineNav.Item aria-label="ovh-cloud" aria-current={tabIndex === 1 ? "page" : undefined} icon={OvhCloudIcon} onSelect={e => {e.preventDefault(); appState.setTab(1.0);}}>
                    OVHcloud
                  </UnderlineNav.Item>
                  <UnderlineNav.Item aria-label="aws" aria-current={tabIndex === 2 ? "page" : undefined} icon={AwsIcon} onSelect={e => {e.preventDefault(); appState.setTab(2.0);}}>
                    AWS
                  </UnderlineNav.Item>
                  <UnderlineNav.Item aria-label="about" aria-current={tabIndex === 3 ? "page" : undefined} icon={CloudGreyIcon} onSelect={e => {e.preventDefault(); appState.setTab(3.0);}}>
                    About
                  </UnderlineNav.Item>
                </UnderlineNav>
              </Box>
              <Box m={3}>
                {(tabIndex === 0) && <ClouderTab/>}
                {(tabIndex === 1) && <OVHcloudTab/>}
                {(tabIndex === 2) && <AWSTab/>}
                {(tabIndex === 3) && <AboutTab version={version}/>}
              </Box>
            </Box>
          </BaseStyles>
        </ThemeProvider>
      </BrandThemeProvider>
    </>
  );
});

export default Clouder;
