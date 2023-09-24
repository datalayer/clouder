import { useState } from 'react';
import { Box, NavList } from '@primer/react';
import { CloudVirtualMachineIcon, KeyOutlineIcon, KubernetesIcon } from '@datalayer/icons-react';
import SSHKeysTab from './ovh/SSH2KeysTab';
import VirtualMachinesTab from './ovh/VirtualMachinesTab';
import KubernetesTab from './ovh/KubernetesTab';

const OVHTab = () => {
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
              SSH Keys
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
          {(nav === 1) && <SSHKeysTab />}
          {(nav === 2) && <VirtualMachinesTab />}
          {(nav === 3) && <KubernetesTab />}
        </Box>
      </Box>
    </>
  );
}

export default OVHTab;
