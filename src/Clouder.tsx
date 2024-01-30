import { useState, useEffect } from 'react';
import { ThemeProvider, BaseStyles, Box } from '@primer/react';
import { UnderlineNav } from '@primer/react';
import { ThemeProvider as BrandThemeProvider } from '@primer/react-brand'
import { CloudGreyIcon, OvhCloudIcon, AwsIcon } from '@datalayer/icons-react';
import { JupyterFrontEnd } from '@jupyterlab/application';
import { requestAPI } from './jupyterlab/handler';
import ClouderTab from './tabs/ClouderTab';
import OVHcloudTab from './tabs/OVHcloudTab';
import AWSTab from './tabs/AWSTab';
import AboutTab from './tabs/AboutTab';
import useStore from "./state/zustand";

import '@primer/react-brand/lib/css/main.css';

export type ClouderProps = {
  jupyterFrontend?: JupyterFrontEnd;
}

const Clouder =(props: ClouderProps): JSX.Element => {
  const [version, setVersion] = useState('');
  const { tab, setTab } = useStore();
  const intTab = Math.floor(tab);
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
                  <UnderlineNav.Item aria-label="clouder" aria-current={intTab === 0 ? "page" : undefined} onSelect={e => {e.preventDefault(); setTab(0.0);}}>
                    Clouder
                  </UnderlineNav.Item>
                  <UnderlineNav.Item aria-label="ovh-cloud" aria-current={intTab === 1 ? "page" : undefined} icon={OvhCloudIcon} onSelect={e => {e.preventDefault(); setTab(1.0);}}>
                    OVHcloud
                  </UnderlineNav.Item>
                  <UnderlineNav.Item aria-label="aws" aria-current={intTab === 2 ? "page" : undefined} icon={AwsIcon} onSelect={e => {e.preventDefault(); setTab(2.0);}}>
                    AWS
                  </UnderlineNav.Item>
                  <UnderlineNav.Item aria-label="about" aria-current={intTab === 3 ? "page" : undefined} icon={CloudGreyIcon} onSelect={e => {e.preventDefault(); setTab(3.0);}}>
                    About
                  </UnderlineNav.Item>
                </UnderlineNav>
              </Box>
              <Box m={3}>
                {(intTab === 0) && <ClouderTab/>}
                {(intTab === 1) && <OVHcloudTab/>}
                {(intTab === 2) && <AWSTab/>}
                {(intTab === 3) && <AboutTab version={version}/>}
              </Box>
            </Box>
          </BaseStyles>
        </ThemeProvider>
      </BrandThemeProvider>
    </>
  );
};

export default Clouder;
