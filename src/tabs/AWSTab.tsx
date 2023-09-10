import { useState } from 'react';
import { Box, NavList } from '@primer/react';
import { CloudVirtualMachineIcon, KeyOutlineIcon, KubernetesIcon } from '@datalayer/icons-react';
import SshKeysTab from './aws/SshKeysTab';
import VirtualMachinesTab from './aws/VirtualMachinesTab';
import KubernetesTab from './aws/KubernetesTab';

const AWSTab = () => {
  const [nav, setNav] = useState(1);
  return (
    <>
      <Box sx={{display: 'flex'}}>
        <Box>
          <NavList sx={{
              '> *': {
                paddingTop: '0px'
              }
            }}>
            <NavList.Item aria-current={nav === 1 ? 'page' : undefined} onClick={e => setNav(1)}>
              <NavList.LeadingVisual>
                <KeyOutlineIcon/>
              </NavList.LeadingVisual>
              Keys
            </NavList.Item>
            <NavList.Item aria-current={nav === 2 ? 'page' : undefined} onClick={e => setNav(2)}>
              <NavList.LeadingVisual>
                <CloudVirtualMachineIcon/>
              </NavList.LeadingVisual>
              Machines
            </NavList.Item>
            <NavList.Item aria-current={nav === 3 ? 'page' : undefined} onClick={e => setNav(3)}>
              <NavList.LeadingVisual>
                <KubernetesIcon/>
              </NavList.LeadingVisual>
              Kubernetes
            </NavList.Item>
          </NavList>
        </Box>
        <Box ml={3} sx={{ width: '100%'}}>
          {(nav === 1) && <SshKeysTab />}
          {(nav === 2) && <VirtualMachinesTab />}
          {(nav === 3) && <KubernetesTab />}
        </Box>
      </Box>
    </>
  );
}

export default AWSTab;
