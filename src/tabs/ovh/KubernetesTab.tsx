import { Box, Text } from '@primer/react';
import { PageHeader } from '@primer/react/drafts';

const VirtualMachineTab = (): JSX.Element => {
  return (
    <>
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title>Kubernetes Clusters</PageHeader.Title>
        </PageHeader.TitleArea>
      </PageHeader>
      <Box>
        <Text>Kubernetes clusters.</Text>
      </Box>
    </>
  );
}

export default VirtualMachineTab;
