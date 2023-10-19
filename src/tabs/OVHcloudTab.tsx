import { Box, NavList } from '@primer/react';
import { ProjectIcon } from '@primer/octicons-react';
import { KeyOutlineIcon, CloudVirtualMachineIcon, KubernetesIcon } from '@datalayer/icons-react';
import ProjectsTab from './ovh/ProjectsTab';
import KeysTab from './ovh/KeysTab';
import VirtualMachinesTab from './ovh/VirtualMachinesTab';
import KubernetesTab from './ovh/KubernetesTab';
import useStore from "../state/zustand";

const OVHcloudTab = () => {
  const { tab, setTab } = useStore();
  return (
    <>
      <Box sx={{display: 'flex'}}>
        <Box>
          <NavList sx={{
              '> *': {
                paddingTop: '0px',
              }
            }}>
            <NavList.Item aria-current={tab === 1.0 ? 'page' : undefined} onClick={e => setTab(1.0)}>
              <NavList.LeadingVisual>
                <ProjectIcon/>
              </NavList.LeadingVisual>
              Projects
            </NavList.Item>
            <NavList.Item aria-current={tab === 1.1 ? 'page' : undefined} onClick={e => setTab(1.1)}>
              <NavList.LeadingVisual>
                <KeyOutlineIcon/>
              </NavList.LeadingVisual>
              Keys
            </NavList.Item>
            <NavList.Item aria-current={tab === 1.2 ? 'page' : undefined} onClick={e => setTab(1.2)}>
              <NavList.LeadingVisual>
                <CloudVirtualMachineIcon/>
              </NavList.LeadingVisual>
              Machines
            </NavList.Item>
            <NavList.Item aria-current={tab === 1.3 ? 'page' : undefined} onClick={e => setTab(1.3)}>
              <NavList.LeadingVisual>
                <KubernetesIcon/>
              </NavList.LeadingVisual>
              Kubernetes
            </NavList.Item>
          </NavList>
        </Box>
        <Box ml={3} sx={{ width: '100%'}}>
          {(tab === 1.0) && <ProjectsTab />}
          {(tab === 1.1) && <KeysTab />}
          {(tab === 1.2) && <VirtualMachinesTab />}
          {(tab === 1.3) && <KubernetesTab />}
        </Box>
      </Box>
    </>
  );
}

export default OVHcloudTab;
